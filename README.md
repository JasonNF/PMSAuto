## PMSAuto

### 项目简介

PMSAuto 是一个将媒体管理工作流与 Telegram Bot + Telegram MiniApp 集成的项目，提供以下能力：
- 机器人指令与 MiniApp 体验（内联打开应用）
- Emby 用户注册、绑定 Telegram、兑换续期码
- 统一后端服务（FastAPI）与前端静态应用（WebApp）

核心目录结构：
- `bot/telegram_bot.py`：aiogram v3 机器人指令
- `tg_service.py`：FastAPI 服务（Webhook、MiniApp API、静态资源）
- `webapp/`：MiniApp 页面与脚本
- `emby_admin_service.py` 与 `emby_admin_models.py`：Emby 管理接口与数据模型

---

### 环境要求

- Python 3.11+
- uv（Python 包与运行器）
- openresty/nginx（生产环境反向代理，HTTPS）
- PostgreSQL/SQLite（本项目默认 SQLite：`emby_admin.db`，已加入 `.gitignore`）

---

### 快速开始（本地开发）

1) 安装 uv
```
curl -LsSf https://astral.sh/uv/install.sh | sh
# 或使用 Homebrew
brew install uv
```

2) 安装依赖
```
uv sync
```

3) 配置环境变量（本地调试时可使用内网隧道 Expose 或暂不设置）
```
export TELEGRAM_BOT_TOKEN="<你的BotToken>"
export EXTERNAL_BASE_URL="https://your.domain"   # 用于设置 webhook 的公网地址
```

4) 启动服务
```
uv run uvicorn tg_service:app --host 0.0.0.0 --port 8000
```

5) 健康检查
```
curl http://127.0.0.1:8000/healthz   # {"ok": true}
```

6) 设置 Telegram Webhook（需要公网地址连接到本服务）
```
curl https://your.domain/tg/setup
```

---

### 生产部署（方案1：服务器 + openresty/nginx + systemd）

目标：在与域名相同的服务器运行 `tg_service:app`，通过 openresty/nginx 将 `/tg/`、`/app/`、`/healthz` 反向代理到 `127.0.0.1:8000`。

1) 服务器准备
- 安装 uv：`curl -LsSf https://astral.sh/uv/install.sh | sh`
- 拉取/上传代码至服务器，例如 `/opt/PMSAuto`

2) 安装依赖
```
cd /opt/PMSAuto
uv sync
```

3) 配置 systemd（示例）
创建 `/etc/systemd/system/pmsauto.service`：
```
[Unit]
Description=PMSAuto Uvicorn Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/PMSAuto
EnvironmentFile=/etc/pmsauto.env
ExecStart=/root/.local/bin/uv run uvicorn tg_service:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

创建 `/etc/pmsauto.env`（权限 600）：
```
TELEGRAM_BOT_TOKEN=你的真实BotToken
EXTERNAL_BASE_URL=https://your.domain
```

载入与启动：
```
sudo systemctl daemon-reload
sudo systemctl enable --now pmsauto
sudo systemctl status pmsauto --no-pager
```

4) openresty/nginx 反向代理（片段示例）
将以下内容合并到你的站点 `server { ... }` 配置：
```
location /tg/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_redirect off;
}

location /app/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_redirect off;
}

location = /healthz {
    proxy_pass http://127.0.0.1:8000/healthz;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_redirect off;
}
```

测试并重载：
```
sudo nginx -t
sudo nginx -s reload
```

验证健康检查：
```
curl -i https://your.domain/healthz
```

5) 设置 Webhook 与命令菜单
```
curl -sS https://your.domain/tg/setup
```

6) Telegram 体验
- 发送 `/start`，消息中会附带“打开 PMSAuto 应用”的按钮
- 点击进入 MiniApp，按需完成“注册/绑定”“兑换码”“刷新状态”等操作

---

### 关键模块说明

- `bot/telegram_bot.py`
  - `/start` 支持 payload 深链，回复内附“打开 PMSAuto 应用”按钮（内联 WebApp）
  - `/help`、`/register`、`/points`（占位）
- `tg_service.py`
  - `GET /tg/setup`：设置 webhook 与命令菜单
  - `POST /app/api/verify`：验证 Telegram WebApp initData（HMAC），返回绑定状态与到期信息
  - `POST /app/api/register`：注册 Emby 用户并绑定当前 Telegram 用户
  - `POST /app/api/bind`：将当前 Telegram 用户绑定到已有 Emby 用户
  - `POST /app/api/redeem`：兑换续期码，延长到期时间
- `webapp/`
  - `index.html`：MiniApp 主界面
  - `app.js`：与后端交互、状态展示与按钮事件

---

### 安全与配置

- 切勿将 `TELEGRAM_BOT_TOKEN`、`ADMIN_BEARER_TOKEN`、数据库文件等敏感信息纳入版本库。
- 本仓库 `.gitignore` 已默认排除 `settings.py`、`emby_admin.db` 等敏感文件。
- 建议生产环境通过 systemd `EnvironmentFile` 注入环境变量。

---

### 故障排查

- Webhook 报错 `502 Bad Gateway`：
  - 检查 openresty 是否已将 `/tg/` 与 `/healthz` 正确反代至 `127.0.0.1:8000`
  - `curl -sS http://127.0.0.1:8000/healthz` 本地应为 `{ "ok": true }`
  - `curl -sS https://api.telegram.org/bot<token>/getWebhookInfo` 查看错误信息

- Bot 无响应：
  - 检查 `systemctl status pmsauto`、`journalctl -u pmsauto -e`
  - 确保 `TELEGRAM_BOT_TOKEN` 正确且服务已重启

- MiniApp 页面无法打开：
  - 检查 `location /app/` 反代是否生效

---

### 推送至 GitHub

使用 HTTPS 远程：
```
git remote add origin https://github.com/JasonNF/PMSAuto.git
git push -u origin main
```

---

### 感谢

- [Rhilip/AutoRclone](https://github.com/Rhilip/AutoRclone)
- [xyou365/AutoRclone](https://github.com/xyou365/AutoRclone)
