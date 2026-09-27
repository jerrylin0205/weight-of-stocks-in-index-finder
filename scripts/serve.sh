#!/bin/bash
# 讓 launchd 常駐執行網頁介面（綁 Tailscale IP）。
set -u
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
unset NODE_OPTIONS

PORT="${1:-8000}"
UV="$(command -v uv || echo /opt/homebrew/bin/uv)"

# Tailscale 還沒起來時，等一下再重試（launchd KeepAlive 會處理）
exec "$UV" run indexfinder serve --tailscale --port "$PORT"
