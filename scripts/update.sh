#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/PMSAuto"
PY="$APP_DIR/.venv/bin/python"
PIP="$APP_DIR/.venv/bin/pip"

if [ ! -d "$APP_DIR" ]; then
  echo "[ERR] $APP_DIR 不存在，请先部署（或将 APP_DIR 修改为你的路径）" >&2
  exit 1
fi

# 1) 获取最新代码
if [ -d "$APP_DIR/.git" ]; then
  echo ">>> git pull"
  git -C "$APP_DIR" pull
else
  echo "[WARN] $APP_DIR 非 git 仓库，跳过 git pull"
fi

# 2) 确保 venv 存在
if [ ! -x "$PY" ]; then
  echo ">>> 创建虚拟环境"
  python3 -m venv "$APP_DIR/.venv"
fi

# 3) 安装依赖（按项目约束）
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -e "$APP_DIR"
# 固定 requests 版本以满足 pyproject 约束
"$PY" -m pip install "uvicorn[standard]" fastapi aiogram apscheduler sqlalchemy "requests==2.31.0"

# 4) 重启并检查
systemctl daemon-reload || true
systemctl restart pmsauto
systemctl status pmsauto --no-pager || true

# 5) 健康检查
curl -sS http://127.0.0.1:8000/healthz || true
