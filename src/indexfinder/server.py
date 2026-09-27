from __future__ import annotations

import threading
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from . import paths
from .config import load_etfs
from .db import DB
from .normalize import split_query
from .query import QueryEngine

app = FastAPI(title="Stock Index Finder", version="0.1.0")

_lock = threading.Lock()
_engine: QueryEngine | None = None
_engine_stamp: str | None = None
_last_checked = 0.0
_CHECK_INTERVAL = 30.0  # 秒。資料庫在遠端 VM，每次請求都探測太浪費，30 秒探一次就好


def get_engine() -> QueryEngine:
    """快取 QueryEngine；SQL Server 有新資料（重新 fetch 過）就重建，但探測頻率有節流。"""
    global _engine, _engine_stamp, _last_checked
    now = time.monotonic()
    with _lock:
        if _engine is not None and (now - _last_checked) < _CHECK_INTERVAL:
            return _engine
        db = DB()
        stamp = db.latest_fetch_stamp()
        if _engine is None or stamp != _engine_stamp:
            _engine = QueryEngine(db, load_etfs(paths.CONFIG_PATH))
            _engine_stamp = stamp
        else:
            db.close()
        _last_checked = now
        return _engine


@app.get("/api/health")
def health():
    db = DB()
    snaps = db.latest_snapshots()
    return {
        "ok": True,
        "etfs_with_data": len(snaps),
        "as_of": {k: v["as_of_date"] for k, v in sorted(snaps.items())},
    }


@app.get("/api/etfs")
def etfs():
    cfgs = load_etfs(paths.CONFIG_PATH)
    latest = DB().latest_snapshots()
    return [
        {
            "ticker": c.ticker,
            "name": c.name,
            "market": c.market,
            "issuer": c.issuer,
            "index": c.index_name,
            "index_provider": c.index_provider,
            "source": c.source,
            "primary": c.primary,
            "as_of": latest[c.ticker]["as_of_date"] if c.ticker in latest else None,
            "holding_count": latest[c.ticker]["holding_count"] if c.ticker in latest else None,
        }
        for c in cfgs
    ]


@app.get("/api/lookup")
def lookup(
    tickers: str = Query(..., description="一或多個股票代號，空白/逗號/頓號分隔"),
    market: str | None = Query(None, description="US / TW，留空為全部"),
):
    tokens = split_query(tickers)
    if not tokens:
        raise HTTPException(400, "請輸入至少一個股票代號")
    if len(tokens) > 30:
        raise HTTPException(400, "一次最多 30 個代號")
    return get_engine().lookup(tokens, market)


app.mount("/", StaticFiles(directory=str(paths.WEB_DIR), html=True), name="web")
