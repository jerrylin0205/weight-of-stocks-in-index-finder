from __future__ import annotations

from pathlib import Path

import yaml

from .models import ETFConfig


GENERATED_NAME = "tw_etfs.generated.yaml"


def load_etfs(path: Path, include_generated: bool = True) -> list[ETFConfig]:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = list(data.get("etfs") or [])

    # 併入自動產生的台股 ETF 清單（indexfinder sync-tw / fetch 產生），已存在的 ticker 不覆蓋
    generated = path.parent / GENERATED_NAME
    if include_generated and generated.exists():
        gdata = yaml.safe_load(generated.read_text(encoding="utf-8")) or {}
        have = {str(r["ticker"]).upper() for r in rows}
        for r in gdata.get("etfs") or []:
            if str(r["ticker"]).upper() not in have:
                rows.append(r)

    out: list[ETFConfig] = []
    seen: set[str] = set()
    for row in rows:
        ticker = str(row["ticker"]).upper()
        if ticker in seen:
            raise ValueError(f"etfs.yaml: 重複的 ticker {ticker}")
        seen.add(ticker)
        out.append(
            ETFConfig(
                ticker=ticker,
                name=row.get("name", ticker),
                market=str(row.get("market", "US")).upper(),
                issuer=row.get("issuer", ""),
                index_name=row["index"],
                index_provider=row.get("index_provider", ""),
                source=row["source"],
                params=row.get("params") or {},
                primary=bool(row.get("primary", False)),
            )
        )
    return out
