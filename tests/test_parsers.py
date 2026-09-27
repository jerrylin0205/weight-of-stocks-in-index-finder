from datetime import date
from pathlib import Path

import pytest

from indexfinder.sources.ishares import _parse as ishares_parse
from indexfinder.sources.ssga import _parse as ssga_parse
from indexfinder.sources.yuanta import _parse as yuanta_parse

FIX = Path(__file__).parent / "fixtures"


def _weight_sum(holdings):
    return sum(h.weight for h in holdings)


def test_ishares_weight_pct_layout():
    text = (FIX / "ivv_holdings.csv").read_text(encoding="utf-8-sig")
    as_of, holdings = ishares_parse(text)
    assert isinstance(as_of, date)
    assert len(holdings) > 400
    assert 97 < _weight_sum(holdings) < 103
    aapl = next(h for h in holdings if h.ticker == "AAPL")
    assert aapl.weight > 1
    assert all(h.asset_class == "Equity" for h in holdings)


def test_ishares_market_weight_layout():
    # S&P 400/600 檔用 "Market Weight" 欄名而非 "Weight (%)"
    text = (FIX / "ijh_holdings.csv").read_text(encoding="utf-8-sig")
    as_of, holdings = ishares_parse(text)
    assert len(holdings) > 300
    assert 97 < _weight_sum(holdings) < 103


def test_ssga_xlsx():
    blob = (FIX / "xlk_holdings.xlsx").read_bytes()
    as_of, holdings = ssga_parse(blob)
    assert isinstance(as_of, date)
    assert 40 < len(holdings) < 120
    assert 97 < _weight_sum(holdings) < 103
    # 不應該混進現金列
    assert all(h.ticker for h in holdings)


def test_yuanta_html():
    html = (FIX / "yuanta_0050.html").read_text(encoding="utf-8")
    as_of, holdings = yuanta_parse(html)
    assert len(holdings) >= 40
    tsmc = next(h for h in holdings if h.ticker == "2330")
    assert tsmc.name == "台積電"
    assert tsmc.weight > 30
    # 期貨列（TX / NYF）不該被當成成分股
    assert all(h.ticker.isdigit() or h.ticker[:-1].isdigit() for h in holdings)


@pytest.mark.parametrize("bad", ["", "not a csv at all", "<!doctype html><html></html>"])
def test_ishares_parse_rejects_garbage(bad):
    with pytest.raises(Exception):
        ishares_parse(bad)
