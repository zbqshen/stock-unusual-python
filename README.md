# stock-unusual-python

`stock_unusual` 实时异动服务的独立 Python 实现，包含实时采集器、Web 页面、REST API 和 WebSocket 接口。

本项目不导入、不依赖 `gotdx`。TDX 二进制协议已在 `stock_unusual/tdx.py` 中独立实现。

## 功能

- 从 TDX 行情服务器采集股票实时异动数据。
- 支持全量扫描和带重叠窗口的增量扫描。
- 使用 PostgreSQL 持久化异动历史、扫描状态和数据缺口。
- 使用 Redis 缓存当日实时异动数据。
- 支持自定义板块查询：
  - 查看全部自定义板块。
  - 选择一个或多个板块。
  - 全选或全不选。
  - 按板块名称包含指定文本筛选。
- 支持 SQLite 股票名称快照，并在 API 返回中补充股票名称。
- 提供 REST API、WebSocket 实时推送和健康检查接口。
- 提供 systemd 服务和定时任务配置。

## 项目组件

- `stock-unusual-web`
  - FastAPI Web 页面。
  - REST API。
  - WebSocket 实时推送。
  - `/health/live` 和 `/health/ready` 健康检查。
- `stock-unusuald`
  - Python 实时采集器。
  - 支持全量扫描、增量扫描、PostgreSQL 存储、Redis 缓存和缺口追踪。
- `stock-name-sync`
  - 从 TDX 更新股票名称快照。
  - 使用 SQLite 原子替换，避免产生不完整快照。

## API 接口

为了兼容原有页面，保留以下接口：

```text
GET /unusual
GET /api/stock_unusual
GET /api/stock-unusual
GET /api/stock_unusual_blocks
GET /api/stock-unusual-blocks
WS  /ws/stock_unusual
WS  /ws/stock-unusual
GET /health/live
GET /health/ready
```

### 查询全部异动

```text
GET /api/stock_unusual?scope=all&limit=100
```

### 查询指定股票

```text
GET /api/stock_unusual?code=600000&limit=100
```

### 查询多个自定义板块

通过重复传递 `block_name` 指定多个板块：

```text
GET /api/stock_unusual?scope=custom&block_mode=selected&block_name=半导体&block_name=机器人&limit=100
```

### 查询板块名称包含指定文本的板块

前端会先根据输入文本筛选板块，再将选中的板块以 `selected` 模式提交。后端也保留按文本筛选的接口能力：

```text
GET /api/stock_unusual?scope=custom&block_mode=contains&block_text=机器人&limit=100
```

## 本地开发

建议使用系统 Python 创建虚拟环境：

```bash
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
```

运行测试：

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q stock_unusual
```

启动 Web 服务：

```bash
.venv/bin/python -m stock_unusual.web
```

执行一次采集验收：

```bash
.venv/bin/python -m stock_unusual.scanner \
  --once \
  --ignore-trading-window
```

默认开发监听地址为：

```text
0.0.0.0:8898
```

这样可以与原 gotdx 服务的 `8888` 端口并行运行。迁移前应先验证新服务的健康检查、API、WebSocket 和页面功能。

## 配置

已安装的 systemd 服务默认读取：

```text
/etc/stock-unusual-python.env
```

项目自身配置使用 `STOCK_UNUSUAL_*` 变量，同时兼容以下已有存储配置变量：

```text
DATABASE_URL
REDIS_ADDR
REDIS_USERNAME
REDIS_PASSWORD
```

常用配置项包括：

```text
STOCK_UNUSUAL_WEB_ADDR=0.0.0.0:8898
STOCK_UNUSUAL_TDX_HOSTS=host1:7709,host2:7709
STOCK_UNUSUAL_MARKETS=0,1,2
STOCK_UNUSUAL_TDX_TIMEOUT=6
STOCK_UNUSUAL_POLL_INTERVAL_SECONDS=1
STOCK_UNUSUAL_PAGE_SIZE=600
STOCK_UNUSUAL_MAX_PAGES=128
STOCK_UNUSUAL_OVERLAP_ROWS=600
STOCK_NAME_DB=/opt/stock-unusual-python/data/stocknames.sqlite3
CUSTOM_BLOCK_MYSQL_CONFIG=/opt/stock-unusual-python/config/mysql.ini
```

不要将包含密码、数据库连接字符串或其他凭据的配置文件提交到 Git 仓库。运行时配置和本地数据已通过 `.gitignore` 排除。

## systemd 部署

部署文件位于：

```text
deploy/stock-unusual-python.service
deploy/stock-unusual-python-web.service
deploy/stock-name-sync.service
deploy/stock-name-sync.timer
```

常用运维命令：

```bash
systemctl status stock-unusual-python.service
systemctl status stock-unusual-python-web.service
systemctl status stock-name-sync.timer

systemctl restart stock-unusual-python.service
systemctl restart stock-unusual-python-web.service

journalctl -u stock-unusual-python.service -n 100 --no-pager
journalctl -u stock-unusual-python-web.service -n 100 --no-pager
```

## 运行入口

默认服务页面：

```text
http://127.0.0.1:8898/unusual
```

默认健康检查：

```text
http://127.0.0.1:8898/health/live
http://127.0.0.1:8898/health/ready
```
