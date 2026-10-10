# 阿里云 ECS 部署说明

推荐部署目录：

```text
/home/admin/gold-monitor
```

原因：

- 你的服务器截图里已经有 `/home/admin`，这是最顺手的业务目录。
- 当前 `systemd` 模板、虚拟环境、SQLite、日志目录都按这个路径准备好了。
- 不建议放到 `/root`，后续维护、权限控制和自动重启都不如普通业务目录清晰。

## 1. 服务器需要安装什么

以 Alibaba Cloud Linux / CentOS 系为例：

```bash
sudo yum install -y python3 python3-pip tar
```

如果系统没有 `python3-venv` 也没关系，下面命令里直接用 `python3 -m venv` 测试即可。

## 2. 上传发布包

先在本地项目根目录打包：

```bash
chmod +x scripts/package_release.sh
./scripts/package_release.sh
```

生成文件位置：

```text
dist/gold-monitor-时间戳.tar.gz
```

然后上传到服务器，例如：

```bash
scp dist/gold-monitor-*.tar.gz admin@你的服务器IP:/home/admin/
```

## 3. 在服务器上解压

```bash
ssh admin@你的服务器IP
cd /home/admin
mkdir -p gold-monitor
tar -xzf gold-monitor-*.tar.gz -C gold-monitor
cd gold-monitor
```

## 4. 创建 Python 虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. 配置环境变量

复制模板：

```bash
cp deploy/linux/gold-monitor.env.example .env.production
```

编辑：

```bash
vi .env.production
```

至少要配置这些：

- `GOLD_MONITOR_SMTP_HOST`
- `GOLD_MONITOR_SMTP_PORT`
- `GOLD_MONITOR_SMTP_SECURITY`
- `GOLD_MONITOR_SMTP_SENDER_EMAIL`
- `GOLD_MONITOR_SMTP_USERNAME`
- `GOLD_MONITOR_SMTP_PASSWORD`

说明：

- 收件人不用写在环境变量里，启动后进入网页配置即可。
- 数据库会自动创建到 `/home/admin/gold-monitor/data/gold_monitor.db`。
- 日志目录是 `/home/admin/gold-monitor/logs`。

## 6. 先手动启动验证

```bash
chmod +x scripts/run_linux.sh
./scripts/run_linux.sh
```

浏览器访问：

```text
http://你的服务器IP:8000
```

如果能打开页面，按 `Ctrl + C` 停掉，继续做常驻部署。

## 7. 配置 systemd 常驻运行

复制服务文件：

```bash
sudo cp deploy/linux/gold-monitor.service /etc/systemd/system/gold-monitor.service
```

如果你的服务器用户名不是 `admin`，需要先改这两行再复制：

- `User=admin`
- `Group=admin`

加载并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable gold-monitor
sudo systemctl start gold-monitor
```

查看状态：

```bash
sudo systemctl status gold-monitor
```

查看日志：

```bash
sudo journalctl -u gold-monitor -f
```

## 8. 放行端口

你至少需要放行 `8000`：

- 阿里云控制台 -> ECS -> 安全组 -> 入方向
- 新增规则：`TCP 8000`

如果系统本机还开了防火墙，也要放行，例如：

```bash
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
```

## 9. 推荐但可选的配置

推荐项：

- 用 Nginx 反向代理到 `127.0.0.1:8000`
- 给页面加 Basic Auth 或只允许内网/VPN 访问
- 不要直接长期暴露管理页面到公网

如果你只是自己用，最省事的方式是：

- 只开放固定 IP
- 或者只通过阿里云内网 / Tailscale / VPN 访问

## 10. 后续更新

每次发新版：

```bash
cd /home/admin
rm -rf gold-monitor.new
mkdir -p gold-monitor.new
tar -xzf gold-monitor-新版.tar.gz -C gold-monitor.new
cp gold-monitor/.env.production gold-monitor.new/
cd gold-monitor.new
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cd /home/admin
mv gold-monitor gold-monitor.bak
mv gold-monitor.new gold-monitor
sudo systemctl restart gold-monitor
```

确认没问题后再删备份：

```bash
rm -rf /home/admin/gold-monitor.bak
```
