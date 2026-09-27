"""SQL Server 連線設定。做法跟 ~/quant_project/db.py 一致：讀 .env，找不到就退回環境變數，
不依賴 python-dotenv。.env 不進版控（見 .gitignore）。

單一真相來源在 quant_project 那台 VM 的 SQL Server（同一個 database，例如 TEJ_DW），
這個專案的表都放在自己的 schema（預設 etf_finder）底下，不動 quant_project 原本的 dbo schema。
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _load_env(path: Path = _ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def schema() -> str:
    _load_env()
    return os.environ.get("DB_SCHEMA", "etf_finder")


def conn_str() -> str:
    _load_env()
    try:
        server = os.environ["DB_SERVER"]
        database = os.environ["DB_DATABASE"]
        uid = os.environ["DB_UID"]
        pwd = os.environ["DB_PWD"]
    except KeyError as e:
        raise RuntimeError(
            f"缺少連線設定 {e}；請複製 .env.example 成 .env 並填入（跟 ~/quant_project/.env 同一套值即可）。"
        ) from e
    driver = os.environ.get("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={uid};"
        f"PWD={pwd};"
        "TrustServerCertificate=yes;"
    )
