from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from ..models import ETFConfig, HoldingsSnapshot

log = logging.getLogger("indexfinder.sources")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": (
        "text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/json,text/html;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
}


class FetchError(RuntimeError):
    """抓取或解析失敗；訊息會直接印給使用者看。"""


class SkipFetch(Exception):
    """來源沒有比 DB 更新的資料，這次跳過（不算失敗）。"""


@dataclass
class FetchContext:
    raw_dir: Path
    manual_dir: Path
    allow_browser: bool = False
    force: bool = False
    # etf_ticker -> 目前 DB 裡最新 snapshot 的 as_of（ISO 字串），來源可據此決定要不要重抓
    existing_as_of: dict[str, str] = field(default_factory=dict)


def http_get(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 45.0,
    retries: int = 3,
    expect: Callable[[httpx.Response], bool] | None = None,
) -> httpx.Response:
    """GET，帶重試與內容檢查（``expect``）。TLS 一律驗證，但用的是 OS 信任庫
    （見 indexfinder.__init__._use_os_trust_store）。"""
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = httpx.get(url, headers=h, params=params, timeout=timeout, follow_redirects=True)
            if r.status_code == 200 and (expect is None or expect(r)):
                return r
            ct = r.headers.get("content-type", "?")
            last = FetchError(f"HTTP {r.status_code} ct={ct} {len(r.content)}B  {r.url}")
        except httpx.HTTPError as e:  # noqa: PERF203
            last = FetchError(f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise last or FetchError(f"{url}: 未知錯誤")


def save_raw(raw_dir: Path, etf_ticker: str, content: bytes, ext: str, as_of: date) -> str:
    d = raw_dir / etf_ticker
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = d / f"{as_of.isoformat()}__{ts}.{ext}"
    p.write_bytes(content)
    return str(p)


def prune_raw(raw_dir: Path, etf_ticker: str, keep: int = 2) -> int:
    """只留該 ETF 最新的 keep 份原始檔，其餘刪掉。回傳刪除數。"""
    d = raw_dir / etf_ticker
    if not d.is_dir():
        return 0
    files = sorted(
        (f for f in d.iterdir() if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for f in files[max(keep, 0):]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def parse_number(value) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").replace("%", "").strip().strip('"').strip()
    if s in ("", "-", "--", "N/A", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clean_text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


class Source:
    key: str = ""

    def fetch(self, cfg: ETFConfig, ctx: FetchContext) -> HoldingsSnapshot:  # pragma: no cover
        raise NotImplementedError
