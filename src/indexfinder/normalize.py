from __future__ import annotations

import re

_TW_CODE = re.compile(r"^([0-9]{4,6}[A-Z]?)")
_TW_SHAPE = re.compile(r"^[0-9]{4,6}[A-Z]?(?:\.TW[O]?| TT)?$", re.I)
_NONALNUM = re.compile(r"[^A-Za-z0-9]")
_US_SUFFIXES = (".TW", ".TWO", " TT", " US", ".N", ".O", ".OQ", ".K", ".A", " EQUITY")


def looks_taiwanese(token: str) -> bool:
    return bool(_TW_SHAPE.match(token.strip()))


def normalize_ticker(raw: str, market: str | None = None) -> str:
    """把使用者輸入或來源檔的代號正規化成單一 canonical 形式。

    - 台股：抽出開頭 4~6 碼數字代號（可含一個字母），例如 ``2330.TW`` -> ``2330``。
    - 美股：轉大寫、去掉常見交易所後綴、移除非英數字元，
      例如 ``BRK.B`` -> ``BRKB``（與 iShares 檔案一致；使用者輸入也會做同樣處理）。
    """
    if not raw:
        return ""
    s = str(raw).strip().upper()
    if not s:
        return ""

    if (market or "").upper() == "TW" or looks_taiwanese(s):
        m = _TW_CODE.match(s)
        if m:
            return m.group(1)

    for suf in _US_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    s = s.split()[0] if s.split() else s
    return _NONALNUM.sub("", s)


def split_query(text: str) -> list[str]:
    """把輸入框字串切成多個代號（支援空白、逗號、分號、頓號分隔）。"""
    return [t for t in re.split(r"[\s,;、，]+", (text or "").strip()) if t]
