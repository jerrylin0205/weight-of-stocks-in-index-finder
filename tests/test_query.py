from datetime import date

import pytest

from indexfinder.models import ETFConfig, Holding, HoldingsSnapshot
from indexfinder.query import QueryEngine


def _cfg(ticker, index, primary=False, market="US"):
    return ETFConfig(
        ticker=ticker,
        name=f"{ticker} ETF",
        market=market,
        issuer="X",
        index_name=index,
        source="manual",
        primary=primary,
    )


class FakeDB:
    """QueryEngine 只需要 latest_snapshots() / holdings_for()；不必真的連資料庫（sqlite 或
    SQL Server 都行）就能測查詢邏輯，也不用扯進網路依賴。"""

    def __init__(self):
        self._snaps: dict[str, dict] = {}
        self._holdings: dict[int, list[dict]] = {}
        self._next_id = 1

    def add(self, etf_ticker, holds, source="manual", as_of=None):
        sid = self._next_id
        self._next_id += 1
        self._snaps[etf_ticker] = {
            "id": sid,
            "etf_ticker": etf_ticker,
            "as_of_date": (as_of or date.today()).isoformat(),
            "source": source,
            "holding_count": len(holds),
        }
        self._holdings[sid] = [
            {"ticker": t, "raw_ticker": t, "name": n, "weight": w} for t, n, w in holds
        ]

    def latest_snapshots(self):
        return dict(self._snaps)

    def holdings_for(self, snapshot_id):
        return list(self._holdings.get(snapshot_id, []))


@pytest.fixture
def engine():
    db = FakeDB()
    db.add("IVV", [("AAPL", "Apple", 7.0), ("MSFT", "MS", 6.0), ("XYZ", "X", 1.0)])
    db.add("SPY", [("AAPL", "Apple", 7.1), ("MSFT", "MS", 6.1)])
    db.add("XLK", [("AAPL", "Apple", 14.0), ("MSFT", "MS", 12.0)])
    db.add("0050", [("2330", "台積電", 57.0), ("2317", "鴻海", 3.0)])

    cfgs = [
        _cfg("IVV", "S&P 500", primary=True),
        _cfg("SPY", "S&P 500"),
        _cfg("XLK", "Tech Sector", primary=True),
        _cfg("0050", "臺灣50指數", primary=True, market="TW"),
    ]
    return QueryEngine(db, cfgs)


def test_single_ticker_sorted_by_weight(engine):
    r = engine.lookup(["AAPL"])
    assert r["mode"] == "single"
    weights = [x["total_weight"] for x in r["results"]]
    assert weights == sorted(weights, reverse=True)
    assert r["results"][0]["index_name"] == "Tech Sector"  # 14% > 7%


def test_single_ticker_groups_etfs_under_index(engine):
    r = engine.lookup(["AAPL"])
    sp500 = next(x for x in r["results"] if x["index_name"] == "S&P 500")
    assert {e["ticker"] for e in sp500["etfs"]} == {"IVV", "SPY"}
    assert sp500["primary_etf"] == "IVV"
    assert sp500["total_weight"] == 7.0  # 取 primary (IVV) 的權重，不是 SPY 的 7.1


def test_multi_ticker_sums_weights(engine):
    r = engine.lookup(["AAPL", "MSFT"])
    assert r["mode"] == "multi"
    tech = next(x for x in r["results"] if x["index_name"] == "Tech Sector")
    assert tech["total_weight"] == pytest.approx(26.0)
    sp = next(x for x in r["results"] if x["index_name"] == "S&P 500")
    assert sp["total_weight"] == pytest.approx(13.0)
    # Tech Sector 總和較高 -> 排在前面
    assert r["results"][0]["index_name"] == "Tech Sector"


def test_multi_ticker_reports_missing(engine):
    r = engine.lookup(["AAPL", "GOOGL"])
    sp = next(x for x in r["results"] if x["index_name"] == "S&P 500")
    assert sp["missing"] == ["GOOGL"]
    assert [m["query"] for m in sp["matched"]] == ["AAPL"]


def test_unknown_ticker(engine):
    r = engine.lookup(["NOPE"])
    assert r["index_count"] == 0
    assert r["unknown_tickers"] == ["NOPE"]


def test_market_filter(engine):
    assert engine.lookup(["AAPL"], "TW")["index_count"] == 0
    assert engine.lookup(["2330"], "TW")["index_count"] == 1


def test_chinese_name_resolution(engine):
    r = engine.lookup(["台積電"])
    assert r["index_count"] == 1
    assert r["results"][0]["index_name"] == "臺灣50指數"
    assert any("2330" in n for n in r["notes"])


def test_no_false_match_on_normalization(engine):
    # 2330 (台股) 不該誤命中任何美股指數
    r = engine.lookup(["2330"])
    assert all(x["market"] == "TW" for x in r["results"])
