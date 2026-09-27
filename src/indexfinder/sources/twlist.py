"""抓 TWSE 上市 ETF 的『代號 ↔ 簡稱』清單，給 SITCA 自動對應用。"""

from __future__ import annotations

import re

import httpx

from .base import DEFAULT_HEADERS

_URL = (
    "https://isin.twse.com.tw/isin/class_main.jsp"
    "?owncode=&stockname=&isincode=&market={m}&issuetype=I"
    "&industry_code=&Page=1&chklike=Y"
)
_ROW = re.compile(r"<td[^>]*>(\d{4,6}[A-Z]?)</td>\s*<td[^>]*>([^<]+)</td>")


def fetch_etf_names() -> dict[str, str]:
    """回傳 {代號: 簡稱}。market 1=上市、2=上櫃。"""
    out: dict[str, str] = {}
    with httpx.Client(headers={**DEFAULT_HEADERS, "Accept-Language": "zh-TW"}, timeout=40) as c:
        for m in ("1", "2"):
            try:
                r = c.get(_URL.format(m=m), follow_redirects=True)
            except httpx.HTTPError:
                continue
            for code, name in _ROW.findall(r.text):
                name = name.strip()
                if name:
                    out.setdefault(code, name)
    return out
