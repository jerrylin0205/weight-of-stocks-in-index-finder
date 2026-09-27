from __future__ import annotations

import os
from datetime import date

from dateutil import parser as dateparser

from ..models import Holding, HoldingsSnapshot
from .base import FetchContext, FetchError, Source, clean_text, http_get, parse_number, save_raw
from ..normalize import normalize_ticker

# Alpha Vantage ETF_PROFILE：完整持股 + 權重，JSON，免簽署即可用 demo key 查少數
# 白名單 symbol（QQQ 剛好在裡面）；一般用途要去 alphavantage.co 免費申請 key
# （25 次/天），設定 ALPHAVANTAGE_API_KEY 環境變數或 params.av_key。
URL = "https://www.alphavantage.co/query"


class AlphaVantageSource(Source):
    key = "alphavantage"

    def fetch(self, cfg, ctx: FetchContext) -> HoldingsSnapshot:
        symbol = cfg.params.get("av_symbol", cfg.ticker)
        api_key = cfg.params.get("av_key") or os.environ.get("ALPHAVANTAGE_API_KEY") or "demo"

        r = http_get(
            URL,
            params={"function": "ETF_PROFILE", "symbol": symbol, "apikey": api_key},
            expect=lambda r: r.headers.get("content-type", "").startswith("application/json"),
        )
        as_of, holdings = _parse(r.json(), cfg.ticker, is_demo_key=api_key == "demo")
        raw = save_raw(ctx.raw_dir, cfg.ticker, r.content, "json", as_of)
        return HoldingsSnapshot(cfg.ticker, as_of, self.key, holdings, raw)


def _parse(data: dict, ticker: str, is_demo_key: bool) -> tuple[date, list[Holding]]:
    holdings_raw = data.get("holdings") or []
    if not holdings_raw:
        note = data.get("Note") or data.get("Information") or data.get("Error Message")
        hint = "demo key 只白名單少數 symbol 可用" if is_demo_key else "檢查 API key / 是否超過額度"
        raise FetchError(
            f"{ticker}: Alpha Vantage 沒回持股" + (f"（{note}）" if note else "") + f"；{hint}"
        )

    as_of = date.today()
    lu = data.get("last_updated")
    if lu:
        try:
            as_of = dateparser.parse(lu).date()
        except (ValueError, OverflowError):
            pass

    holdings: list[Holding] = []
    for h in holdings_raw:
        sym = clean_text(h.get("symbol"))
        name = clean_text(h.get("description"))
        weight = parse_number(h.get("weight"))
        if not sym or weight is None or weight <= 0:
            continue
        holdings.append(Holding(normalize_ticker(sym, "US"), sym, name, weight * 100))

    if not holdings:
        raise FetchError(f"{ticker}: Alpha Vantage 回應解析不到有效持股")
    return as_of, holdings
