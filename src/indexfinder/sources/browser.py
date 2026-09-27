"""headless 瀏覽器 helper：給需要跑 JS 或過 bot 牆的來源用。

需要額外安裝：
    uv sync --extra browser
    uv run playwright install chromium
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("indexfinder.browser")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class BrowserUnavailable(RuntimeError):
    pass


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise BrowserUnavailable(
            "未安裝 playwright；請執行 "
            "`uv sync --extra browser && uv run playwright install chromium`"
        ) from e
    # 某些終端會設一個指向不存在檔案的 NODE_OPTIONS，會讓 playwright 的 node driver 掛掉
    if "NODE_OPTIONS" in os.environ and "restore-node-options" in os.environ["NODE_OPTIONS"]:
        os.environ.pop("NODE_OPTIONS", None)
    return sync_playwright


def render_page(
    url: str,
    *,
    click_selectors: list[str] | None = None,
    wait_selector: str | None = None,
    wait_ms: int = 2500,
    locale: str = "en-US",
    timeout_ms: int = 90000,
) -> str:
    """載入 ``url``，（依序點掉 ``click_selectors``，例如「展開」），回傳 render 後的 HTML。"""
    sync_playwright = _playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_context(user_agent=_UA, locale=locale).new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(wait_ms)
            for sel in click_selectors or []:
                try:
                    el = page.query_selector(sel)
                    if el:
                        el.click()
                        page.wait_for_timeout(1500)
                except Exception as e:  # noqa: BLE001
                    log.debug("點擊 %s 失敗（可忽略）：%s", sel, e)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:  # noqa: BLE001
                    pass
            return page.content()
        finally:
            browser.close()


def fetch_bytes(url: str, warmup: str | None = None, timeout_ms: int = 45000) -> bytes | None:
    """開瀏覽器（可先造訪 ``warmup`` 拿 cookie），再用瀏覽器 context 抓 ``url`` 的原始 bytes。"""
    sync_playwright = _playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=_UA, locale="en-US")
            if warmup:
                try:
                    ctx.new_page().goto(warmup, timeout=timeout_ms, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    log.debug("warmup 失敗（可忽略）：%s", e)
            resp = ctx.request.get(url, timeout=timeout_ms)
            if resp.ok:
                return resp.body()
            log.warning("browser fetch %s -> HTTP %s", url, resp.status)
            return None
        finally:
            browser.close()
