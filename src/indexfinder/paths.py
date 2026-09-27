from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"  # 原始下載檔（審計用），本機保留；結構化資料在 SQL Server（見 dbconfig.py）
MANUAL_DIR = DATA_DIR / "manual"
CONFIG_PATH = ROOT / "config" / "etfs.yaml"
WEB_DIR = ROOT / "web"

for _d in (DATA_DIR, RAW_DIR, MANUAL_DIR):
    _d.mkdir(parents=True, exist_ok=True)
