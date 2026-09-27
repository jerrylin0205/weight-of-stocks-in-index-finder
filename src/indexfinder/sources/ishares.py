from __future__ import annotations

import csv
import re
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

# 現代 iShares 美股持股下載：只認 product id，slug 可隨意。
# 舊的 /<magic>.ajax?fileType=csv 路由已失效（會回傳整頁 HTML）。
URL = "https://www.ishares.com/us/products/{pid}/x/latest-holdings.csv"

_AS_OF = re.compile(r"holdings\s+as\s+of\D+([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4})", re.I)


def _pick_key(fieldnames, *candidates: str) -> str | None:
    lower = {(f or "").strip().lower(): f for f in fieldnames}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def _looks_like_csv(r) -> bool:
    head = r.content[:200].lstrip().lower()
    return not head.startswith((b"<!doctype", b"<html"))


class ISharesSource(Source):
    key = "ishares"

    def fetch(self, cfg, ctx: FetchContext) -> HoldingsSnapshot:
        pid = str(cfg.params["product_id"])
        r = http_get(URL.format(pid=pid), expect=_looks_like_csv)
        text = r.content.decode("utf-8-sig", errors="replace")
        if text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            raise FetchError(
                f"{cfg.ticker}: iShares 回傳網頁而非 CSV（IP 可能被擋）；"
                f"改用 --browser 或放 data/manual/{cfg.ticker}.csv"
            )
        fund_name = text.splitlines()[0].strip() if text.strip() else ""
        expect_name = cfg.params.get("expect_name")
        if expect_name and expect_name.lower() not in fund_name.lower():
            raise FetchError(
                f"{cfg.ticker}: product_id {pid} 抓到的是「{fund_name}」，"
                f"預期包含「{expect_name}」——product_id 可能填錯"
            )
        as_of, holdings = _parse(text)
        raw = save_raw(ctx.raw_dir, cfg.ticker, r.content, "csv", as_of)
        return HoldingsSnapshot(cfg.ticker, as_of, self.key, holdings, raw)


def _parse(text: str) -> tuple[date, list[Holding]]:
    lines = text.splitlines()
    as_of: date | None = None
    header_idx: int | None = None

    for i, ln in enumerate(lines):
        if as_of is None:
            m = _AS_OF.search(ln)
            if m:
                try:
                    as_of = dateparser.parse(m.group(1)).date()
                except (ValueError, OverflowError):
                    pass
        if ln.lstrip().lower().startswith(("ticker,", '"ticker"')):
            header_idx = i
            break

    if header_idx is None:
        raise FetchError("iShares CSV: 找不到 'Ticker,...' 標題列")

    reader = csv.DictReader(lines[header_idx:])
    # iShares 有兩種欄位配置：舊檔用 "Weight (%)"，S&P 400/600 等新檔用 "Market Weight"
    weight_key = _pick_key(
        reader.fieldnames or [], "Weight (%)", "Market Weight", "Weight", "Market Weight (%)"
    )
    if weight_key is None:
        raise FetchError(f"iShares CSV: 找不到權重欄位，欄位有 {reader.fieldnames}")

    holdings: list[Holding] = []
    for row in reader:
        tk = clean_text(row.get("Ticker"))
        asset_class = clean_text(row.get("Asset Class"))
        weight = parse_number(row.get(weight_key))
        name = clean_text(row.get("Name"))
        if not tk or tk == "-":
            continue
        if asset_class and asset_class != "Equity":
            continue
        if weight is None or weight <= 0:
            continue
        holdings.append(Holding(normalize_ticker(tk, "US"), tk, name, weight, asset_class or "Equity"))

    return as_of or date.today(), holdings
