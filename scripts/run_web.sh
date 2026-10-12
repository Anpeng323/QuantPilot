#!/bin/bash
# 启动 Quant DB Web 控制台
# 用法:
#   bash scripts/run_web.sh                # 默认 mock 模式
#   bash scripts/run_web.sh --live          # 读取 ~/.quant_env 里的 key
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate

# 首次启动自动装依赖
if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "首次启动，安装依赖..."
  pip install --quiet fastapi uvicorn
fi

# 检查 ~/.quant_env 配置
ENV_FILE="$HOME/.quant_env"
if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
fi

# 处理参数
MODE="mock"
while [ $# -gt 0 ]; do
  case "$1" in
    --live) MODE="real"
      if [ -z "$LLM_PROVIDER" ]; then export LLM_PROVIDER=minimax; fi
      ;;
    --mock) MODE="mock" ;;
    *) ;;
  esac
  shift
done

if [ "$MODE" = "mock" ]; then
  export LLM_PROVIDER=mock
fi

echo ""
echo "🚀 Quant DB Web 控制台（$MODE 模式）"
[ "$MODE" = "real" ] && echo "🔑 LLM provider: $LLM_PROVIDER / ${MINIMAX_MODEL:-${QWEN_MODEL:-${DEEPSEEK_MODEL:-default}}}"
echo "📊 访问 http://localhost:8765"
echo ""

# 如果是 minimax 但没设 key，提示
if [ "$LLM_PROVIDER" = "minimax" ] && [ -z "$MINIMAX_API_KEY" ]; then
  echo "⚠️  未设 MINIMAX_API_KEY，将回退到 mock 模式"
  echo "   设置方法: echo 'export MINIMAX_API_KEY=sk-xxx' >> ~/.quant_env"
  export LLM_PROVIDER=mock
fi

# 启动前按端口清理占用 8765 的旧进程（无论之前用何种方式启动，避免端口冲突导致页面打不开）
if command -v lsof >/dev/null 2>&1 && lsof -ti :8765 >/dev/null 2>&1; then
  lsof -ti :8765 | xargs -r kill -9 2>/dev/null || true
  sleep 1
fi

PYTHONUNBUFFERED=1 python3 -m web.app
