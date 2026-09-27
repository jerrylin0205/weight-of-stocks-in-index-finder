from __future__ import annotations

import datetime as _dt
import logging
import time

import pyodbc

from . import dbconfig
from .models import HoldingsSnapshot

log = logging.getLogger("indexfinder.db")


def _ddl(schema: str) -> list[str]:
    """回傳一串獨立執行的 DDL 陳述式（SQL Server 的 CREATE VIEW 等要單獨一個 batch）。"""
    return [
        f"""IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = '{schema}')
            EXEC('CREATE SCHEMA {schema}')""",
        f"""IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='snapshots' AND schema_id=SCHEMA_ID('{schema}'))
            CREATE TABLE {schema}.snapshots (
              id            INT IDENTITY(1,1) PRIMARY KEY,
              etf_ticker    NVARCHAR(20) NOT NULL,
              as_of_date    DATE NOT NULL,
              fetched_at    DATETIME2 NOT NULL,
              source        NVARCHAR(30) NOT NULL,
              holding_count INT NOT NULL,
              total_weight  FLOAT NOT NULL,
              raw_path      NVARCHAR(500) NULL,
              CONSTRAINT uq_{schema}_snapshots UNIQUE (etf_ticker, as_of_date)
            )""",
        f"""IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='holdings' AND schema_id=SCHEMA_ID('{schema}'))
            CREATE TABLE {schema}.holdings (
              snapshot_id INT NOT NULL REFERENCES {schema}.snapshots(id) ON DELETE CASCADE,
              ticker      NVARCHAR(20) NOT NULL,
              raw_ticker  NVARCHAR(40) NULL,
              name        NVARCHAR(200) NULL,
              weight      FLOAT NOT NULL,
              asset_class NVARCHAR(30) NULL,
              CONSTRAINT pk_{schema}_holdings PRIMARY KEY (snapshot_id, ticker)
            )""",
        f"""IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_{schema}_holdings_ticker')
            CREATE INDEX idx_{schema}_holdings_ticker ON {schema}.holdings(ticker)""",
        f"""IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_{schema}_snap_etf')
            CREATE INDEX idx_{schema}_snap_etf ON {schema}.snapshots(etf_ticker, as_of_date)""",
        f"""CREATE OR ALTER VIEW {schema}.latest_snapshots AS
            SELECT s.* FROM {schema}.snapshots s
            JOIN (SELECT etf_ticker, MAX(fetched_at) AS mf FROM {schema}.snapshots GROUP BY etf_ticker) m
              ON m.etf_ticker = s.etf_ticker AND m.mf = s.fetched_at""",
        f"""CREATE OR ALTER VIEW {schema}.latest_holdings AS
            SELECT s.etf_ticker, s.as_of_date, s.source, h.ticker, h.name, h.weight, h.asset_class
            FROM {schema}.holdings h JOIN {schema}.latest_snapshots s ON s.id = h.snapshot_id""",
    ]


def _val(v):
    """DATE/DATETIME2 讀回來轉成字串，跟原本 sqlite（ISO 字串）行為一致，
    上層（query.py / cli.py / server.py）都當字串比較 / json 序列化，不用改。"""
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def _rows(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [{c: _val(v) for c, v in zip(cols, row)} for row in cursor.fetchall()]


class DB:
    KEEP_SNAPSHOTS = 2  # 每檔 ETF 保留幾次抓取（1 個現用 + 1 個可回溯/比對）

    def __init__(self, conn: str | None = None, schema: str | None = None, retries: int = 5):
        self.schema = schema or dbconfig.schema()
        cs = conn or dbconfig.conn_str()
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self.conn = pyodbc.connect(cs, timeout=10)
                break
            except pyodbc.Error as e:  # noqa: PERF203
                last = e
                if attempt < retries:
                    log.warning(
                        "SQL Server 連線失敗（第 %d/%d 次），5 秒後重試：%s", attempt, retries, e
                    )
                    time.sleep(5)
        else:
            raise last  # type: ignore[misc]
        self.conn.autocommit = False
        cur = self.conn.cursor()
        for stmt in _ddl(self.schema):
            cur.execute(stmt)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- writes -------------------------------------------------------------
    def upsert_snapshot(self, snap: HoldingsSnapshot) -> int:
        s = self.schema
        cur = self.conn.cursor()
        cur.fast_executemany = True
        cur.execute(
            f"DELETE FROM {s}.snapshots WHERE etf_ticker=? AND as_of_date=?",
            (snap.etf_ticker, snap.as_of_date),
        )
        cur.execute(
            f"""INSERT INTO {s}.snapshots
                (etf_ticker, as_of_date, fetched_at, source, holding_count, total_weight, raw_path)
                OUTPUT INSERTED.id
                VALUES (?,?,?,?,?,?,?)""",
            (
                snap.etf_ticker,
                snap.as_of_date,
                snap.fetched_at,
                snap.source,
                len(snap.holdings),
                snap.total_weight,
                snap.raw_path,
            ),
        )
        sid = int(cur.fetchone()[0])
        # 同一檔正規化後撞代號的（例如兩種股份分類經正規化後同名）只留最後一筆，
        # 跟舊版 sqlite 的 INSERT OR REPLACE 行為一致，避免違反 (snapshot_id, ticker) 主鍵。
        dedup: dict[str, object] = {h.ticker: h for h in snap.holdings if h.ticker}
        rows = [
            (sid, h.ticker, h.raw_ticker, h.name, h.weight, h.asset_class)
            for h in dedup.values()
        ]
        if rows:
            cur.executemany(
                f"""INSERT INTO {s}.holdings
                    (snapshot_id, ticker, raw_ticker, name, weight, asset_class)
                    VALUES (?,?,?,?,?,?)""",
                rows,
            )
        # 只保留每檔 ETF 最近 N 次抓取（依 fetched_at），舊的連同 holdings 一起刪（FK cascade）
        cur.execute(
            f"""WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY etf_ticker ORDER BY fetched_at DESC
                    ) rn
                    FROM {s}.snapshots WHERE etf_ticker=?
                )
                DELETE FROM {s}.snapshots WHERE id IN (SELECT id FROM ranked WHERE rn > ?)""",
            (snap.etf_ticker, self.KEEP_SNAPSHOTS),
        )
        self.conn.commit()
        return sid

    def prune(self, known_tickers: set[str]) -> dict:
        """刪掉不在 config 裡的 ETF 的所有資料。回傳清掉了什麼。"""
        s = self.schema
        cur = self.conn.cursor()
        orphan = [
            r[0]
            for r in cur.execute(f"SELECT DISTINCT etf_ticker FROM {s}.snapshots").fetchall()
            if r[0] not in known_tickers
        ]
        for t in orphan:
            cur.execute(f"DELETE FROM {s}.snapshots WHERE etf_ticker=?", (t,))
        self.conn.commit()
        return {"removed_etfs": orphan}

    # ---- reads ------------------------------------------------------------
    def latest_snapshots(self) -> dict[str, dict]:
        cur = self.conn.cursor()
        cur.execute(f"SELECT * FROM {self.schema}.latest_snapshots")
        return {r["etf_ticker"]: r for r in _rows(cur)}

    def holdings_for(self, snapshot_id: int) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT * FROM {self.schema}.holdings WHERE snapshot_id=? ORDER BY weight DESC",
            (snapshot_id,),
        )
        return _rows(cur)

    def row_counts(self) -> tuple[int, int]:
        cur = self.conn.cursor()
        n_snap = cur.execute(f"SELECT COUNT(*) FROM {self.schema}.snapshots").fetchone()[0]
        n_hold = cur.execute(f"SELECT COUNT(*) FROM {self.schema}.holdings").fetchone()[0]
        return int(n_snap), int(n_hold)

    def latest_fetch_stamp(self) -> str:
        """輕量『資料有沒有變』探針：不用把全部持股讀回來就能判斷要不要重建 QueryEngine。"""
        cur = self.conn.cursor()
        cur.execute(f"SELECT MAX(fetched_at) FROM {self.schema}.snapshots")
        row = cur.fetchone()
        return _val(row[0]) if row and row[0] is not None else ""
