# 浙商积存金异常波动邮件监测工具

基于 `FastAPI + APScheduler + SQLite + SMTP` 的常驻服务。

## 功能

- 秒级自动检测浙商积存金（`productSku=1961543816`）
- 计算最近 1 小时窗口的最高/最低/差值/幅度
- 触发条件：`delta >= 阈值` **或** `pct >= 阈值`
- 自适应轮询：默认 30 秒检测一次，接近阈值时切换为 5 秒检测一次
- 告警邮件冷却（默认 60 分钟）
- 开放访问配置页面：SMTP、收件人、阈值、运行状态、告警记录
- 支持手动“发送测试邮件”和“立即检测一次”

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开浏览器：

- http://127.0.0.1:8000

## API

- `GET /api/config`：读取配置（密码脱敏）
- `PUT /api/config`：更新配置
- `POST /api/actions/test-email`：发送测试邮件
- `POST /api/actions/run-check`：立即执行检测
- `GET /api/status`：查看运行状态
- `GET /api/alerts?limit=N`：告警记录

## 告警策略

默认配置：

- `threshold_delta=3.0`：最近 1 小时高低差达到 3 元/克触发
- `threshold_pct=0.30`：最近 1 小时振幅达到 0.30% 触发
- `poll_interval_seconds=30`：普通检测间隔
- `fast_poll_interval_seconds=5`：接近阈值后的快速检测间隔
- `adaptive_threshold_ratio=0.70`：达到任一阈值 70% 时进入快速检测
- `cooldown_minutes=60`：告警邮件发送成功后 60 分钟内不重复发送

## 邮件配置说明

推荐在服务端预置 SMTP 默认值，页面只填写收件人即可：

```bash
export GOLD_MONITOR_SMTP_HOST=smtp.qq.com
export GOLD_MONITOR_SMTP_PORT=465
export GOLD_MONITOR_SMTP_SECURITY=ssl
export GOLD_MONITOR_SMTP_USERNAME=your-account@qq.com
export GOLD_MONITOR_SMTP_PASSWORD=your-smtp-auth-code
export GOLD_MONITOR_SMTP_SENDER_EMAIL=your-account@qq.com
```

也兼容不带 `GOLD_MONITOR_` 前缀的变量名，例如 `SMTP_HOST`。

页面的“高级 SMTP 设置”支持 SMTP 三种模式：

- `none`
- `starttls`
- `ssl`

至少需配置：

- SMTP host/port
- sender_email
- recipients（可多个）

> 注意：页面按需求为开放访问，请在受信任网络部署并做好访问控制。

## 数据文件

SQLite 默认路径：

- `data/gold_monitor.db`

表结构：

- `settings`
- `alert_events`
- `run_logs`
