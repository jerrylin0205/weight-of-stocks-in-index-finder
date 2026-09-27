from datetime import date
from pathlib import Path

from indexfinder.sources.sitca import (
    _norm,
    _quarter_end,
    _parse,
    match_funds_to_codes,
)

FIX = Path(__file__).parent / "fixtures"


def test_quarter_end():
    assert _quarter_end("202606") == date(2026, 6, 30)
    assert _quarter_end("202603") == date(2026, 3, 31)
    assert _quarter_end("202512") == date(2025, 12, 31)


def test_parse_ah11_fixture():
    html = (FIX / "sitca_ah11.html").read_text(encoding="utf-8")
    funds = _parse(html)
    assert len(funds) > 30
    # 元大台灣卓越50（= 0050）應在其中，且台積電是最大持股
    name = next(n for n in funds if "元大台灣卓越50" in n)
    holdings = funds[name]
    assert holdings[0].ticker == "2330"
    assert holdings[0].weight > 40
    # 只收台股：代號都是數字
    assert all(h.ticker[:4].isdigit() for h in holdings)
    # SITCA 只揭露 ≥1%
    assert all(h.weight >= 1.0 for h in holdings)


def test_norm_strips_boilerplate():
    assert _norm("元大台灣卓越50基金") == "元大台灣卓越50"
    assert _norm("統一台股升級50主動式ETF基金 (基金之配息來源可能為收益平準金)") == "統一台股升級50"
    assert _norm("富邦臺灣公司治理100基金") == "富邦台灣公司治理100"  # 臺->台


def test_match_funds_to_codes():
    twse = {
        "0050": "元大台灣50",
        "006208": "富邦台50",
        "00919": "群益台灣精選高息",
        "0056": "元大高股息",
    }
    funds = [
        "元大台灣卓越50基金",
        "富邦台灣釆吉50基金 (本基金之配息來源可能為收益平準金)",
        "群益台灣精選高息ETF基金 (...)",
    ]
    matched, missed = match_funds_to_codes(funds, twse, skip=set())
    assert matched.get("0050", "").startswith("元大台灣卓越50")
    assert matched.get("006208", "").startswith("富邦台灣釆吉50")
    assert matched.get("00919", "").startswith("群益台灣精選高息")


def test_match_respects_skip():
    twse = {"0050": "元大台灣50"}
    matched, _ = match_funds_to_codes(["元大台灣卓越50基金"], twse, skip={"0050"})
    assert "0050" not in matched
