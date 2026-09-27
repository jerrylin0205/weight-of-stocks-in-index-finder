#!/bin/bash
# 安裝 / 更新 每天早上自動抓資料的 launchd 排程（macOS）。
#   安裝：  bash scripts/install-schedule.sh
#   指定時間： bash scripts/install-schedule.sh 7 30      # 07:30
#   移除：  bash scripts/install-schedule.sh --uninstall
set -euo pipefail

LABEL="com.stock-index-finder.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "已移除排程。"
  exit 0
fi

HOUR="${1:-8}"
MIN="${2:-0}"

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_DIR/scripts/daily-fetch.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>$HOUR</integer>
        <key>Minute</key><integer>$MIN</integer>
    </dict>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>$PROJECT_DIR/data/launchd.out.log</string>
    <key>StandardErrorPath</key><string>$PROJECT_DIR/data/launchd.err.log</string>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF

chmod +x "$PROJECT_DIR/scripts/daily-fetch.sh"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf '已安裝：每天 %02d:%02d 自動執行 fetch + prune\n' "$HOUR" "$MIN"
echo "  plist： $PLIST"
echo "  log：   $PROJECT_DIR/data/fetch.log"
echo "  手動測試： bash $PROJECT_DIR/scripts/daily-fetch.sh"
echo "  看下次執行： launchctl print gui/$(id -u)/$LABEL | grep -A3 'next fire'"
