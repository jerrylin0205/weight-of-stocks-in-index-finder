from __future__ import annotations

import csv
from datetime import date

from dateutil import parser as dateparser

from ..models import Holding, HoldingsSnapshot
from .base import FetchContext, FetchError, Source, clean_text, parse_number
from ..normalize import normalize_ticker

# 任何抓不到的 ETF 的保底來源，也適合放權威資料（TEJ / 發行商月報 / Bloomberg 匯出）。
# 檔案：data/manual/<TICKER>.csv
# 欄位（大小寫不拘，可用中文）：ticker,name,weight[,as_of]
#   ticker / code / 代碼         必填
#   weight / weight (%) / 權重   必填，單位為百分比（7.06 代表 7.06%）
#   name / 名稱                   選填
#   as_of                        選填，整份檔案共用一個資料日期

_TICKER_KEYS = ("ticker", "code", "symbol", "代碼", "股票代碼", "商品代碼")
_WEIGHT_KEYS = ("weight", "weight (%)", "weight(%)", "weighting", "權重", "比重", "商品權重")
_NAME_KEYS = ("name", "security name", "名稱", "股票名稱", "商品名稱")


def _pick(row: dict, keys) -> str:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return ""


class ManualSource(Source):
    key = "manual"

    def fetch(self, cfg, ctx: FetchContext) -> HoldingsSnapshot:
        path = ctx.manual_dir / f"{cfg.ticker}.csv"
        if not path.exists():
            raise FetchError(
                f"{cfg.ticker}: 找不到 {path}；請建立此檔，"
                "欄位 ticker,name,weight[,as_of]"
            )
        text = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(text.splitlines())
        holdings: list[Holding] = []
        as_of: date | None = None
        for raw in reader:
            row = {clean_text(k).lower(): clean_text(v) for k, v in raw.items() if k}
            tk = _pick(row, _TICKER_KEYS)
            weight = parse_number(_pick(row, _WEIGHT_KEYS))
            name = _pick(row, _NAME_KEYS)
            if row.get("as_of") and as_of is None:
                try:
                    as_of = dateparser.parse(row["as_of"]).date()
                except (ValueError, OverflowError):
                    pass
            if not tk or weight is None or weight <= 0:
                continue
            holdings.append(Holding(normalize_ticker(tk, cfg.market), tk, name, weight))

        if not holdings:
            raise FetchError(f"{cfg.ticker}: {path} 沒有有效的持股列")
        return HoldingsSnapshot(
            cfg.ticker, as_of or date.today(), self.key, holdings, str(path)
        )
