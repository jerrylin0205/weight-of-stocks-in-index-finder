from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class ETFConfig:
    """config/etfs.yaml 裡的一列。"""

    ticker: str
    name: str
    market: str  # "US" | "TW"
    issuer: str
    index_name: str
    source: str  # sources.REGISTRY 的 key
    params: dict = field(default_factory=dict)
    primary: bool = False  # 是否為該指數的「代表性 ETF」（權重以它為準）
    index_provider: str = ""


@dataclass
class Holding:
    ticker: str  # 正規化後
    raw_ticker: str  # 來源檔原始代號
    name: str
    weight: float  # 百分比，例如 7.06 代表 7.06%
    asset_class: str = "Equity"


@dataclass
class HoldingsSnapshot:
    etf_ticker: str
    as_of_date: date
    source: str
    holdings: list[Holding]
    raw_path: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_weight(self) -> float:
        return round(sum(h.weight for h in self.holdings), 4)
