from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from ..models import Holding, HoldingsSnapshot
from .base import FetchContext, FetchError, Source, http_get, parse_number, save_raw
from ..normalize import normalize_ticker

# 元大投信 ETF 持股頁（Nuxt SPA）。SSR 只吐前 5 檔，完整清單要在瀏覽器點「展開」後才 render，
# 所以這個 source 需要 --browser。
URL = "https://www.yuantaetfs.com/product/detail/{fid}/ratio"

_DATE_NEAR = re.compile(
    r"(?:資料日期|基準日|資料截止日|持股資料日期)[：:\s]*?(20\d{2}[/.\-]\d{1,2}[/.\-]\d{1,2})"
)
_DATE_ANY = re.compile(r"(20\d{2})[/.\-](\d{1,2})[/.\-](\d{1,2})")
_CODE = re.compile(r"^\d{4,6}[A-Z]?$")


class YuantaSource(Source):
    key = "yuanta"

    def fetch(self, cfg, ctx: FetchContext) -> HoldingsSnapshot:
        fid = cfg.params.get("fund_id", cfg.ticker)
        url = URL.format(fid=fid)

        html: str
        if ctx.allow_browser:
            from .browser import BrowserUnavailable, render_page

            try:
                html = render_page(
                    url,
                    click_selectors=["[class*=more]"],  # 「展開」
                    wait_selector="div.tr",
                    locale="zh-TW",
                )
            except BrowserUnavailable as e:
                raise FetchError(f"{cfg.ticker}: {e}") from e
        else:
            r = http_get(
                url,
                headers={"Accept-Language": "zh-TW,zh;q=0.9"},
                expect=lambda r: "商品權重" in r.text or "商品代碼" in r.text,
            )
            html = r.text

        as_of, holdings = _parse(html)
        raw = save_raw(ctx.raw_dir, cfg.ticker, html.encode("utf-8"), "html", as_of)
        if len(holdings) < 5:
            hint = "" if ctx.allow_browser else "；加 --browser 取得完整持股，或用 data/manual/ 手動檔"
            raise FetchError(
                f"{cfg.ticker}: 只解析到 {len(holdings)} 檔持股{hint}"
            )
        return HoldingsSnapshot(cfg.ticker, as_of, self.key, holdings, raw)


def _cell_text(td) -> str:
    spans = td.find_all("span")
    if spans:
        return spans[-1].get_text(strip=True)
    return td.get_text(strip=True)


def _parse(html: str) -> tuple[date, list[Holding]]:
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    as_of: date | None = None
    m = _DATE_NEAR.search(page_text) or _DATE_ANY.search(page_text)
    if m:
        try:
            as_of = dateparser.parse(m.group(1).replace(".", "/").replace("-", "/")).date()
        except (ValueError, OverflowError):
            as_of = None

    holdings: dict[str, Holding] = {}
    for tr in soup.select("div.tr"):
        tds = tr.select("div.td")
        if len(tds) < 4:
            continue
        code = _cell_text(tds[0])
        name = _cell_text(tds[1])
        weight = parse_number(_cell_text(tds[3]))
        # 期貨 / 現金列的代碼不是 4~6 碼數字，會被濾掉
        if not _CODE.match(code) or weight is None or weight <= 0:
            continue
        holdings.setdefault(normalize_ticker(code, "TW"), Holding(
            normalize_ticker(code, "TW"), code, name, weight
        ))

    return as_of or date.today(), list(holdings.values())
