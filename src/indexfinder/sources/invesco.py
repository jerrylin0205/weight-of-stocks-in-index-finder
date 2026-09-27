from __future__ import annotations

import csv
import io
from datetime import date

from dateutil import parser as dateparser

from ..models import Holding, HoldingsSnapshot
from .base import (
    FetchContext,
    FetchError,
    Source,
    clean_text,
    http_get,
    parse_number,
    save_raw,
)
from ..normalize import normalize_ticker

URL = "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0"


def _looks_like_csv(r) -> bool:
    head = r.content[:400].lower()
    return b"," in head and not head.lstrip().startswith((b"<!doctype", b"<html", b"{"))


class InvescoSource(Source):
    key = "invesco"

    def fetch(self, cfg, ctx: FetchContext) -> HoldingsSnapshot:
        tk = cfg.params.get("invesco_ticker", cfg.ticker)
        params = {"audienceType": "Investor", "action": "download", "ticker": tk}
        referer = (
            "https://www.invesco.com/us/financial-products/etfs/product-detail"
            f"?audienceType=Investor&ticker={tk}"
        )
        content: bytes
        try:
            r = http_get(URL, params=params, headers={"Referer": referer}, expect=_looks_like_csv)
            content = r.content
        except FetchError as e:
            content = self._browser_fallback(cfg, ctx, tk, e)

        as_of, holdings = _parse(content.decode("utf-8-sig", errors="replace"))
        raw = save_raw(ctx.raw_dir, cfg.ticker, content, "csv", as_of)
        return HoldingsSnapshot(cfg.ticker, as_of, self.key, holdings, raw)

    def _browser_fallback(self, cfg, ctx: FetchContext, tk: str, err: Exception) -> bytes:
        if not ctx.allow_browser:
            raise FetchError(
                f"{cfg.ticker}: Invesco 直連被擋（{err}）；"
                f"加 --browser 或放 data/manual/{cfg.ticker}.csv"
            )
        from .browser import BrowserUnavailable, fetch_bytes

        url = f"{URL}?audienceType=Investor&action=download&ticker={tk}"
        try:
            data = fetch_bytes(url, warmup="https://www.invesco.com/us/financial-products/etfs")
        except BrowserUnavailable as e:
            raise FetchError(f"{cfg.ticker}: {e}") from e
        if not data or not _bytes_look_like_csv(data):
            raise FetchError(
                f"{cfg.ticker}: Invesco 直連與瀏覽器 fallback 都失敗（{err}）；"
                f"請放 data/manual/{cfg.ticker}.csv"
            )
        return data


def _bytes_look_like_csv(b: bytes) -> bool:
    head = b[:400].lower()
    return b"," in head and not head.lstrip().startswith((b"<!doctype", b"<html"))


def _parse(text: str) -> tuple[date, list[Holding]]:
    reader = csv.DictReader(io.StringIO(text))
    holdings: list[Holding] = []
    as_of: date | None = None
    for raw in reader:
        row = {clean_text(k): clean_text(v) for k, v in raw.items() if k}
        tk = row.get("Holding Ticker") or row.get("Ticker") or ""
        name = row.get("Name") or row.get("Security Name") or ""
        weight = parse_number(
            row.get("Weight") or row.get("Weighting") or row.get("PercentageOfFund")
        )
        d = row.get("Date") or row.get("As Of Date")
        if d and as_of is None:
            try:
                as_of = dateparser.parse(d).date()
            except (ValueError, OverflowError):
                pass
        if not tk or weight is None or weight <= 0:
            continue
        if "cash" in name.lower() and not tk.isalpha():
            continue
        holdings.append(Holding(normalize_ticker(tk, "US"), tk, name, weight))

    if not holdings:
        raise FetchError("Invesco CSV: 解析不到任何持股（欄位格式可能已改）")
    return as_of or date.today(), holdings
