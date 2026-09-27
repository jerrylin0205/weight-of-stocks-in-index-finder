from __future__ import annotations

from .alphavantage import AlphaVantageSource
from .base import FetchContext, FetchError, SkipFetch, Source
from .invesco import InvescoSource
from .ishares import ISharesSource
from .manual import ManualSource
from .sitca import SitcaSource
from .ssga import SSGASource
from .yuanta import YuantaSource

REGISTRY: dict[str, Source] = {
    s.key: s
    for s in (
        ISharesSource(),
        SSGASource(),
        InvescoSource(),
        AlphaVantageSource(),
        YuantaSource(),
        SitcaSource(),
        ManualSource(),
    )
}

__all__ = ["REGISTRY", "FetchContext", "FetchError", "SkipFetch", "Source"]
