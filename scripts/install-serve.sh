#!/bin/bash
# 讓網頁介面開機自動啟動並常駐（掛掉自動重啟），綁 Tailscale IP。
#   安裝：  bash scripts/install-serve.sh
#   指定 port： bash scripts/install-serve.sh 9000
#   移除：  bash scripts/install-serve.sh --uninstall
set -euo pipefail

LABEL="com.stock-index-finder.serve"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "已移除常駐 serve。"
  exit 0
fi

PORT="${1:-8000}"
mkdir -p "$HOME/Library/LaunchAgents"
chmod +x "$PROJECT_DIR/scripts/serve.sh"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_DIR/scripts/serve.sh</string>
        <string>$PORT</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>$PROJECT_DIR/data/serve.log</string>
    <key>StandardErrorPath</key><string>$PROJECT_DIR/data/serve.log</string>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
IP="$([ -x "$TS" ] && "$TS" ip -4 2>/dev/null | head -1 || echo '<你的 Tailscale IP>')"
echo "已安裝：開機自動啟動、常駐、掛掉自動重啟"
echo "  網址：  http://$IP:$PORT   （任何裝置連上 Tailscale 後都能開）"
echo "  log：   $PROJECT_DIR/data/serve.log"
echo "  停止：  bash scripts/install-serve.sh --uninstall"
