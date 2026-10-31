# NetOps Console

企业网络智能运维诊断系统。项目采集真实网络设备状态作为证据，检索本地运维知识库，
再由大模型生成可追溯的结构化诊断建议。

系统自动执行的设备命令限于只读白名单。配置变更始终由人工审批、备份、执行和复测；
不存在模型直接下发配置的路径。

## 快速启动

前置条件：

- Python 3.12 x64
- Node.js 18 或更高版本
- 首次安装依赖和下载 Embedding 模型时可访问网络

在项目根目录执行。

Windows PowerShell：

```powershell
python start.py
```

Linux / macOS：

```bash
python3 start.py
# 项目 .venv 已存在时，优先使用项目解释器
.venv/bin/python start.py
```

这是唯一用户启动入口。它会逐级检查并准备：

1. Python 版本和项目 `.venv`；
2. 后端依赖；
3. 项目根 `.env` 和登录令牌；
4. 知识库索引；
5. Node.js、npm 和前端依赖；
6. React 控制台构建；
7. FastAPI 服务。

默认启动为 Mock 模式，不连接设备，也不会自动采集：

```text
运行模式: MOCK（示例数据，不连接真实设备）
访问地址: http://127.0.0.1:8000
```

按 `Ctrl+C` 停止服务。启动器会等待 Web 进程退出并完成清理。

## 启动参数

| 参数 | 作用 |
|---|---|
| `--mock` | 使用示例数据，默认 |
| `--live` | 使用真实 API；采集仍受 `APP_AUTO_COLLECT` 控制 |
| `--check` | 只检查环境，不安装、构建或启动 |
| `--skip-install` | 缺少依赖时不自动安装 |
| `--skip-build` | 复用已有 `frontend/dist`，仅用于 Live 模式 |
| `--skip-index` | 知识库索引缺失时不自动构建 |

查看完整帮助：

```powershell
# Windows
python start.py --help
```

```bash
# Linux / macOS
python3 start.py --help
.venv/bin/python start.py --help
```

## Linux 运行说明

以下命令适用于 Ubuntu/Debian 等常见 Linux 发行版。项目仍以根目录 `start.py` 为唯一启动入口。

### 1. 检查环境

```bash
cd /path/to/project6

python3 --version      # 需要 Python 3.12 x64
node --version         # 需要 Node.js 18+
npm --version
```

首次启动时 `start.py` 会自动创建 `.venv` 并安装缺失依赖。也可以手动准备：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/pip install -r requirements/dev.txt
```

### 2. 启动服务

```bash
# Mock 模式：示例数据，不连接真实设备
python3 start.py

# Live 模式：使用真实 API 和设备数据
python3 start.py --live

# 项目 .venv 已存在时，推荐直接使用
.venv/bin/python start.py --live

# 只检查环境，不安装、不构建、不启动
.venv/bin/python start.py --check
```

服务默认监听 `http://127.0.0.1:8000`，前台运行时按 `Ctrl+C` 停止。

需要后台运行时：

```bash
nohup .venv/bin/python start.py --live >/tmp/netops-console.log 2>&1 &
echo $! >/tmp/netops-console.pid

# 查看日志 / 停止
tail -f /tmp/netops-console.log
kill "$(cat /tmp/netops-console.pid)"
```

端口被占用时，可在 `.env` 中修改 `APP_PORT`，或临时覆盖：

```bash
APP_PORT=8001 .venv/bin/python start.py --live
```

### 3. 首次登录

首次启动会在日志中显示随机 `APP_API_TOKEN`，后续可从 `.env` 读取：

```bash
grep '^APP_API_TOKEN=' .env
```

浏览器打开 `http://127.0.0.1:8000/login`，输入该口令。

### 4. 真实网络接入（Linux）

ZeroTier、EVE-NG 路由和设备网络与应用启动解耦：

```bash
chmod +x scripts/connect_remote_network_legacy.sh
sudo scripts/connect_remote_network_legacy.sh --no-collect
```

脚本只读取项目 `.env`。普通 `python3 start.py` 不会执行 `sudo`、修改系统路由或连接
远程实验网。管理网路由持久化、ZeroTier peer 检查和 EVE 入口说明见：

- [docs/NETWORK_CONNECTION_GUIDE.md](docs/NETWORK_CONNECTION_GUIDE.md)

### 5. 开发与测试

```bash
# 后端测试和静态检查
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q collector control rag_engine knowledge_base ops_tools webapp

# 前端测试和构建
cd frontend
npm test
npm run build
```

后端热重载开发：

```bash
.venv/bin/python -m uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000
```

前端独立开发：

```bash
cd frontend
VITE_USE_MOCK_API=false npm run dev
```

### 6. Linux 常见问题

| 现象 | 处理 |
|---|---|
| `python: command not found` | 使用 `python3`，或安装 Python 3.12 |
| `No module named ...` | 使用 `.venv/bin/python`，不要用系统 Python |
| `zerotier-cli` 提示缺少 authtoken | 使用 `sudo zerotier-cli ...` |
| 脚本 `Permission denied` | `chmod +x scripts/connect_remote_network_legacy.sh` |
| 端口被占用 | 修改 `.env` 的 `APP_PORT`，或停止已有进程 |
| 启动日志出现 `telnetlib` 弃用警告 | Netmiko 已知警告，不影响运行 |

## 访问入口

- 登录页：`http://127.0.0.1:8000/login`
- React 控制台：`http://127.0.0.1:8000/console/overview`
- 旧版总览：`http://127.0.0.1:8000/legacy`
- 旧版业务页面：`/diagnose`、`/changes`、`/knowledge`、`/evaluations`

首次生成 `.env` 时，启动日志会显示随机 `APP_API_TOKEN`。后续可在 `.env` 中查看或
修改，长度不得少于 16 位。

## 主要能力

- 设备台账：新增、修改、软退役、导入导出和当前状态；
- 自动采集：SSH、SNMP、Ping、后台任务和采集计划；
- 拓扑：站点、设备、链路和采集证据；
- 诊断：会话、模型调用、知识命中、证据关联和报告；
- 知识库：文章版本、Embedding 模型和索引构建；
- 人工终端：备份提醒、写命令确认和完整审计；
- 人工变更：审批、备份、步骤执行、复测和事件流；
- 设置：LLM、Embedding 和本地环境变量的受控更新。

## 配置

应用只读取项目根目录 `.env`。主要配置：

```dotenv
APP_HOST=127.0.0.1
APP_PORT=8000
APP_API_TOKEN=至少16位的随机令牌
APP_AUTO_COLLECT=false

LLM_API_KEY=your_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_MAX_TOKENS=3000
LLM_TEMPERATURE=0.2
LLM_TIMEOUT=60

DEVICE_USERNAME=admin
DEVICE_PASSWORD=change_me
SNMP_COMMUNITY=public_ro

SQLITE_DB_PATH=./data/ops.db
CHROMA_DB_PATH=./data/chroma
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
FASTEMBED_CACHE_PATH=./models/fastembed
```

完整模板见 `.env.example`。`.env`、数据、模型缓存和报告不会提交到 Git。

## 项目结构

```text
start.py              唯一启动入口
app_bootstrap.py      数据库、运行状态和可选采集初始化
collector/            设备、采集、解析、保留策略和数据库访问
control/              人工变更、审批、备份和终端会话
rag_engine/           证据、Prompt、LLM 和诊断
knowledge_base/       知识文章、Embedding 和 Chroma
ops_tools/            只读工具边界
webapp/               FastAPI、v1 API、旧版路由和登录页
frontend/             React + TypeScript 控制台
scripts/              与 Web 启动解耦的 Linux 运维脚本
docs/                 架构、开发、运维和设计文档
```

React 控制台是默认业务前端；旧版 Jinja 页面在迁移期间继续可用，旧 URL 不变。

## 开发与验证

以下命令以 Windows PowerShell 为例；Linux 对应命令见前文“Linux 运行说明”。

只检查环境：

```powershell
python start.py --check
```

后端测试：

```powershell
python -m pytest
python -m ruff check .
```

前端测试：

```powershell
Set-Location frontend
npm test
npm run build
```

独立开发前端时，先在后端终端启动 FastAPI，再在 `frontend/` 运行 `npm run dev`。
Vite 会将 `/api`、`/login` 和 `/logout` 代理到 `127.0.0.1:8000`。

## 真实网络接入

ZeroTier、EVE-NG 路由和设备网络准备与应用启动解耦。

- Windows：使用 ZeroTier 客户端和系统网络工具手工接入；
- Linux：可使用 `scripts/connect_remote_network_legacy.sh`；
- 完整步骤：[docs/NETWORK_CONNECTION_GUIDE.md](docs/NETWORK_CONNECTION_GUIDE.md)。

普通 `python start.py` 不会执行 `sudo`、修改系统路由或连接远程实验网。

## 安全边界

- 单口令鉴权使用常量时间比较，浏览器登录态为 HttpOnly Cookie。
- API Key、设备口令和登录令牌不会以明文从设置接口返回。
- 自动命令必须在只读白名单中。
- 模型只输出建议，不直接执行命令或下发配置。
- 打开终端前必须确认配置备份，写命令需要二次确认。
- 命令、输出、错误和会话结束原因都会进入审计。
- `/docs`、`/redoc` 和 OpenAPI 默认关闭。

## 文档

- [系统架构](docs/ARCHITECTURE.md)
- [开发指南](docs/DEVELOPMENT.md)
- [运维指南](docs/OPERATIONS.md)
- [前端开发](docs/FRONTEND.md)
- [API 约定](docs/API.md)
- [数据库设计](docs/DATABASE_DESIGN.md)
- [控制台设计](docs/DESIGN.md)
- [网络连接](docs/NETWORK_CONNECTION_GUIDE.md)
- [路线图](docs/ROADMAP.md)
