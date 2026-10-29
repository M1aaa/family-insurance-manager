#!/usr/bin/env bash
# 家庭保险整理工具 - 本地一键启动 (类 Unix)
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"
PYEXE="${PYEXE:-python3}"

echo "============================================"
echo "  家庭保险整理工具 - 本地一键启动 (Unix)"
echo "============================================"

if [ ! -x ".venv/bin/python" ]; then
    echo "[1/3] 创建虚拟环境 .venv ..."
    "$PYEXE" -m venv .venv
else
    echo "[1/3] 检测到已存在的虚拟环境 .venv，跳过创建。"
fi

echo "[2/3] 安装依赖 ..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo "[3/3] 启动服务： http://127.0.0.1:${PORT}"
echo "       （按 Ctrl+C 停止）"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
