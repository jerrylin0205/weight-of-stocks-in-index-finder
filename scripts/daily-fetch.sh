#!/bin/bash
# 每天自動更新 ETF 持股。由 launchd 呼叫（見 install-schedule.sh），也可手動跑。
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

# launchd 不會帶終端機的環境；把可能有問題的 NODE_OPTIONS 清掉，補上 Homebrew 路徑
unset NODE_OPTIONS
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

LOG="$PROJECT_DIR/data/fetch.log"
mkdir -p "$PROJECT_DIR/data"

# log 超過 2MB 就砍掉重來
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 2097152 ]; then
  tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

UV="$(command -v uv || echo /opt/homebrew/bin/uv)"

# 早上剛開機/喚醒時，VM 的虛擬網路介面可能還沒起來；等它通了再跑，最多等 90 秒。
_wait_for_db() {
  local hostport host port
  hostport="$(grep -m1 '^DB_SERVER=' "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r')"
  host="${hostport%%,*}"; port="${hostport##*,}"
  [ -z "$host" ] && return 0
  for _ in $(seq 1 30); do
    nc -z -G 2 "$host" "${port:-1433}" 2>/dev/null && return 0
    sleep 3
  done
  echo "!! 等了 90 秒，$host:$port 還是連不到，照樣試著跑（會靠 DB() 內建重試）"
}

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') fetch start ====="
  _wait_for_db
  "$UV" run indexfinder fetch --browser --sleep 1
  echo "----- prune -----"
  "$UV" run indexfinder prune
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') done ====="
  echo
} >> "$LOG" 2>&1
