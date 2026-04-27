# 京东积存金异常波动监测工具（可通过邮件推送告警）

基于 `FastAPI + APScheduler + SQLite + SMTP` 的积存金价格监测服务。后端通过 APScheduler 定时拉取积存金最新价格，计算最近 1 小时窗口内的高低差和振幅，并在达到阈值时发送邮件提醒；前端页面通过 WebSocket 接收状态和告警更新。

服务内置一个轻量 Web 配置页面，启动后可以在浏览器中配置 SMTP、收件人、告警阈值、运行状态和告警记录，适合部署为局域网或服务器上的常驻监控服务。

## 主要功能

- 后端通过定时任务拉取积存金价格，默认 30 秒检测一次。
- 支持浙商、民生、工银三个数据源，默认启用浙商。
- 计算最近 1 小时窗口的当前价、最高价、最低价、高低差和百分比振幅。
- 告警触发条件：`delta >= threshold_delta` 或 `pct >= threshold_pct`。
- 自适应轮询：接近阈值时自动切换为更快的检测间隔。
- 支持邮件告警冷却，避免短时间内重复发送相同方向的提醒。
- 支持极值突破提醒，价格继续突破上次告警极值时可再次发送邮件。
- 内置 Web UI，可修改配置、发送测试邮件、手动触发检测、查看运行状态和告警记录。
- 提供 REST API，并通过 WebSocket 向前端推送状态和告警更新，方便二次开发或接入其它系统。
- 使用 SQLite 保存配置、告警事件、运行日志和本地价格采样。
- 自动清理过期数据，默认保留 1 天。

## 技术栈

- Python 3.10+
- FastAPI
- Uvicorn
- APScheduler
- Pydantic v2
- Requests
- SQLite

## 目录结构

```text
.
├── app/
│   ├── main.py       # FastAPI 入口、路由、调度器生命周期
│   ├── monitor.py    # 监测任务编排、告警判断、邮件发送
│   ├── market.py     # 积存金数据源配置和价格抓取
│   ├── models.py     # 配置和 API 数据模型
│   ├── db.py         # SQLite 初始化和数据读写
│   ├── logic.py      # 阈值、收件人、告警方向等业务逻辑
│   └── mailer.py     # SMTP 邮件发送
├── static/
│   └── index.html    # 配置和监控页面
├── tests/            # 单元测试
├── data/             # 运行时 SQLite 数据目录，默认不提交
├── logs/             # 运行日志目录，默认不提交
└── requirements.txt
```

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后打开：

- 本机访问：http://127.0.0.1:8000
- 局域网访问：http://你的服务器IP:8000

FastAPI 文档页面：

- Swagger UI：http://127.0.0.1:8000/docs
- OpenAPI JSON：http://127.0.0.1:8000/openapi.json

## 配置方式

配置有两种来源：

1. Web 页面或 API 写入 SQLite 的持久化配置。
2. 环境变量提供的默认 SMTP 配置。

服务启动时会自动创建 `data/gold_monitor.db`。如果数据库里没有配置，会写入默认配置；如果 SMTP 字段为空，会使用对应环境变量作为默认值。

### SMTP 环境变量

推荐在部署环境中预置 SMTP 默认值，页面只维护收件人和告警阈值：

```bash
export GOLD_MONITOR_SMTP_HOST=smtp.qq.com
export GOLD_MONITOR_SMTP_PORT=465
export GOLD_MONITOR_SMTP_SECURITY=ssl
export GOLD_MONITOR_SMTP_USERNAME=your-account@qq.com
export GOLD_MONITOR_SMTP_PASSWORD=your-smtp-auth-code
export GOLD_MONITOR_SMTP_SENDER_NAME=浙商积存金提醒
export GOLD_MONITOR_SMTP_SENDER_EMAIL=your-account@qq.com
export GOLD_MONITOR_SMTP_SUBJECT_PREFIX="[浙商]"
```

也兼容不带 `GOLD_MONITOR_` 前缀的变量名，例如：

```bash
export SMTP_HOST=smtp.qq.com
export SMTP_PORT=465
export SMTP_SECURITY=ssl
export SMTP_USERNAME=your-account@qq.com
export SMTP_PASSWORD=your-smtp-auth-code
export SMTP_SENDER_EMAIL=your-account@qq.com
```

SMTP 安全模式支持：

- `none`
- `starttls`
- `ssl`

至少需要配置：

- `host`
- `port`
- `sender_email`
- `recipients`

如果 SMTP 服务需要认证，还需要配置：

- `username`
- `password`

## 默认告警策略

默认参数位于 `app/models.py` 的 `AlertSettings`：

| 配置项 | 默认值 | 说明 |
| --- | ---: | --- |
| `threshold_delta` | `3.0` | 最近 1 小时高低差达到 3 元/克触发 |
| `threshold_pct` | `0.30` | 最近 1 小时振幅达到 0.30% 触发 |
| `near_extreme_pct` | `0.10` | 当前价接近窗口高/低点时显示极值状态 |
| `cooldown_minutes` | `60` | 同方向邮件告警冷却时间 |
| `poll_interval_seconds` | `30` | 普通检测间隔 |
| `fast_poll_interval_seconds` | `5` | 接近阈值或触发告警后的快速检测间隔 |
| `adaptive_threshold_ratio` | `0.70` | 达到任一阈值 70% 时进入快速检测 |
| `extreme_breakthrough_delta` | `2.0` | 冷却期内继续突破上次告警极值 2 元/克时允许再次告警 |
| `retention_days` | `7` | 告警、日志和采样数据保留天数 |

## API 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | Web 配置页面 |
| `GET` | `/api/config` | 读取当前配置，SMTP 密码会脱敏 |
| `PUT` | `/api/config` | 更新 SMTP、告警、数据源和启停配置 |
| `POST` | `/api/actions/test-email` | 发送测试邮件 |
| `POST` | `/api/actions/run-check` | 立即执行一次检测 |
| `GET` | `/api/status` | 查看调度器、最近运行、最近告警等状态 |
| `GET` | `/api/alerts?limit=20` | 查询最近告警记录，`limit` 范围 1-200 |
| `POST` | `/api/utils/parse-recipients` | 解析收件人文本 |
| `WS` | `/ws/updates` | 推送状态快照和最近告警 |

### 更新配置示例

```bash
curl -X PUT http://127.0.0.1:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "enabled_sources": ["zheshang"],
    "smtp": {
      "recipients": ["dev@example.com"]
    },
    "alert": {
      "threshold_delta": 3,
      "threshold_pct": 0.3,
      "poll_interval_seconds": 30,
      "fast_poll_interval_seconds": 5
    }
  }'
```

### 手动检测示例

```bash
curl -X POST http://127.0.0.1:8000/api/actions/run-check
```

### 测试邮件示例

```bash
curl -X POST http://127.0.0.1:8000/api/actions/test-email \
  -H "Content-Type: application/json" \
  -d '{"recipients":["dev@example.com"]}'
```

## 数据文件

SQLite 默认路径：

```text
data/gold_monitor.db
```

主要表：

- `settings`：持久化配置。
- `alert_events`：告警记录和邮件发送结果。
- `run_logs`：每次检测或清理任务的运行日志。
- `price_samples`：本地价格采样，用于补足 1 小时窗口计算。

`data/*.db`、`logs/`、`.venv/`、`.playwright-mcp/`、`.tmp_jd/` 等运行时文件已在 `.gitignore` 中排除。

## 开发和测试

安装依赖后运行测试：

```bash
pytest
```

开发时可以开启自动重载：

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 部署建议

- 不要把 SMTP 授权码、GitHub token 等敏感信息写入代码或提交到仓库。
- Web 页面当前没有登录鉴权，建议只部署在受信任网络，或在前面加 Nginx Basic Auth、VPN、内网访问控制等保护。
- 生产环境建议使用 `systemd`、`supervisor`、Docker 或其它进程管理工具保持服务常驻。
- 如果部署在公网环境，请限制访问来源，并确认 SMTP、数据库目录和日志目录权限。
