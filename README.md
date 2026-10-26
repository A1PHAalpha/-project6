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

在项目根目录执行：

```powershell
python start.py
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
python start.py --help
```

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
