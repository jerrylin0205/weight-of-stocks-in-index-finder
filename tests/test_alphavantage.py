import json
from datetime import date
from pathlib import Path

import pytest

from indexfinder.sources.alphavantage import _parse
from indexfinder.sources.base import FetchError

FIX = Path(__file__).parent / "fixtures"


def test_parse_qqq_fixture():
    data = json.loads((FIX / "alphavantage_qqq.json").read_text())
    as_of, holdings = _parse(data, "QQQ", is_demo_key=True)
    assert as_of == date(2026, 9, 11)
    tickers = {h.ticker for h in holdings}
    assert tickers == {"NVDA", "AAPL", "MSFT"}  # 0% CASH row dropped
    nvda = next(h for h in holdings if h.ticker == "NVDA")
    assert nvda.weight == pytest.approx(8.51)  # fraction -> percent


def test_parse_empty_holdings_raises():
    with pytest.raises(FetchError, match="demo key"):
        _parse({"Note": "rate limited"}, "QQQ", is_demo_key=True)


def test_parse_empty_holdings_non_demo_hint():
    with pytest.raises(FetchError, match="檢查 API key"):
        _parse({}, "QQQ", is_demo_key=False)
