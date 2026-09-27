from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .db import DB
from .models import ETFConfig
from .normalize import looks_taiwanese, normalize_ticker


@dataclass
class MatchedHolding:
    query: str  # 使用者原始輸入
    ticker: str  # 正規化代號
    name: str
    weight: float


@dataclass
class IndexResult:
    index_name: str
    index_provider: str
    market: str
    etfs: list[dict]  # 追蹤此指數、且 config 有列的所有 ETF
    primary_etf: str  # 權重取自哪一檔 ETF 的持股
    as_of: str
    source: str
    total_weight: float  # 命中的查詢股票權重加總（單一代號時就是那一檔的權重）
    matched: list[dict]
    missing: list[str]  # 有查、但不在此指數的代號
    holding_count: int


@dataclass
class LookupResponse:
    mode: str  # "single" | "multi"
    query: list[str]
    normalized: list[str]
    unknown_tickers: list[str]  # 任何指數都沒命中的代號
    index_count: int
    results: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class QueryEngine:
    def __init__(self, db: DB, etf_configs: list[ETFConfig]):
        self.db = db
        self.configs = etf_configs
        self.latest = db.latest_snapshots()

        # 把每檔 ETF 的最新持股一次讀進記憶體： {etf: {ticker: {name, weight}}}
        self.holdings: dict[str, dict[str, dict]] = {}
        for etf, snap in self.latest.items():
            self.holdings[etf] = {
                r["ticker"]: {"name": r["name"], "weight": r["weight"], "raw": r["raw_ticker"]}
                for r in db.holdings_for(snap["id"])
            }

        # 依指數分組
        self.by_index: dict[str, list[ETFConfig]] = {}
        for c in etf_configs:
            self.by_index.setdefault(c.index_name, []).append(c)

    # ------------------------------------------------------------------
    def _representative(self, cfgs: list[ETFConfig], qset: set[str]) -> ETFConfig | None:
        """挑一檔『代表性 ETF』：優先 primary 且有資料，其次命中查詢股票最多的。"""
        candidates = []
        for c in cfgs:
            if c.ticker not in self.holdings:
                continue
            overlap = len(qset & self.holdings[c.ticker].keys())
            candidates.append((0 if c.primary else 1, -overlap, c.ticker, c))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][3]

    def _resolve_names(self, token: str) -> str | None:
        """token 不像代號時，嘗試用持股名稱做包含比對（台股中文名很常見）。"""
        needle = token.strip().lower()
        if len(needle) < 2:
            return None
        for etf_holdings in self.holdings.values():
            for tk, info in etf_holdings.items():
                if needle in (info["name"] or "").lower():
                    return tk
        return None

    # ------------------------------------------------------------------
    def lookup(self, raw_tokens: list[str], market: str | None = None) -> dict:
        market = (market or "").upper() or None
        tokens = [t for t in (t.strip() for t in raw_tokens) if t]
        notes: list[str] = []

        qmap: dict[str, str] = {}  # normalized -> raw
        for raw in tokens:
            hint = "TW" if (market == "TW" or looks_taiwanese(raw)) else market
            norm = normalize_ticker(raw, hint)
            if not norm or not _is_ticker_like(norm):
                # 不像代號（例如打了中文名「台積電」）-> 用持股名稱做比對
                resolved = self._resolve_names(raw)
                if resolved:
                    notes.append(f"「{raw}」對應到代號 {resolved}")
                    norm = resolved
                elif not norm:
                    notes.append(f"「{raw}」無法解析為代號")
                    continue
            qmap[norm] = raw

        qset = set(qmap)
        results: list[IndexResult] = []

        for index_name, cfgs in self.by_index.items():
            if market and all(c.market != market for c in cfgs):
                continue
            rep = self._representative(cfgs, qset)
            if rep is None:
                continue
            rep_holdings = self.holdings[rep.ticker]
            matched = [
                MatchedHolding(qmap[n], n, rep_holdings[n]["name"], round(rep_holdings[n]["weight"], 4))
                for n in qmap
                if n in rep_holdings
            ]
            if not matched:
                continue
            snap = self.latest[rep.ticker]
            results.append(
                IndexResult(
                    index_name=index_name,
                    index_provider=rep.index_provider,
                    market=rep.market,
                    etfs=[
                        {
                            "ticker": c.ticker,
                            "name": c.name,
                            "issuer": c.issuer,
                            "has_data": c.ticker in self.holdings,
                        }
                        for c in cfgs
                    ],
                    primary_etf=rep.ticker,
                    as_of=snap["as_of_date"],
                    source=snap["source"],
                    total_weight=round(sum(m.weight for m in matched), 4),
                    matched=[asdict(m) for m in sorted(matched, key=lambda m: -m.weight)],
                    missing=[qmap[n] for n in qmap if n not in rep_holdings],
                    holding_count=snap["holding_count"],
                )
            )

        results.sort(key=lambda r: -r.total_weight)

        known: set[str] = set()
        for r in results:
            known.update(m["ticker"] for m in r.matched)
        unknown = [qmap[n] for n in qmap if n not in known]

        resp = LookupResponse(
            mode="single" if len(qmap) <= 1 else "multi",
            query=tokens,
            normalized=list(qmap),
            unknown_tickers=unknown,
            index_count=len(results),
            results=[asdict(r) for r in results],
            notes=notes,
        )
        return asdict(resp)


def _is_ticker_like(token: str) -> bool:
    t = token.upper()
    if t.isdigit() and 4 <= len(t) <= 6:
        return True
    return t.isascii() and t.isalnum() and len(t) <= 6 and any(c.isalpha() for c in t)
