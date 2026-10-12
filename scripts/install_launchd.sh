#!/bin/bash
# 安装/卸载 launchd 定时任务
PLIST_SRC="/Users/quanlin/Desktop/项目/AI量化/quant_db/launchd/com.quanlin.quant.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.quanlin.quant.daily.plist"

case "${1:-install}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl unload "$PLIST_DST" 2>/dev/null
    launchctl load "$PLIST_DST"
    echo "✅ 已安装并加载：$PLIST_DST"
    echo "每日 17:00 自动跑 etl.daily_update（含日K + 5min K + 指数）"
    launchctl list | grep quanlin.quant || true
    ;;
  uninstall)
    launchctl unload "$PLIST_DST" 2>/dev/null
    rm -f "$PLIST_DST"
    echo "❎ 已卸载"
    ;;
  run-now)
    cd /Users/quanlin/Desktop/项目/AI量化/quant_db
    source .venv/bin/activate
    PYTHONUNBUFFERED=1 python3 -m etl.daily_update --datalen 30 --minute-datalen 100
    ;;
  status)
    launchctl list | grep quanlin.quant || echo "未安装"
    ;;
  *)
    echo "用法: $0 [install|uninstall|run-now|status]"
    exit 1
    ;;
esac
