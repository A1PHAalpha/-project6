# 系统架构

## 1. 总体定位

NetOps Console 是一个模块化单体应用：Web、采集、RAG、诊断和人工变更流程运行在同一
Python 服务中，通过普通函数调用协作。生产环境只需要一个 FastAPI 进程，不依赖独立的
Node 服务、消息队列或外部数据库。

```mermaid
flowchart LR
    U[浏览器或脚本] --> A[FastAPI 应用]
    A --> N[React Console]
    A --> O[旧版 Jinja 页面]
    N --> V1[/api/v1]
    O --> API[/旧版 API]
    V1 --> S[领域服务]
    API --> S
    S --> D[(SQLite)]
    S --> K[(Chroma 知识库)]
    S --> T[SSH / SNMP / Ping]
    S --> L[OpenAI 兼容 LLM]
```

## 2. 代码边界

```text
app_bootstrap.py      启动时初始化数据库、运行状态和可选的采集任务
start.py              唯一用户启动入口，检查环境并构建前后端
collector/            设备台账、采集、解析、保留策略和数据库访问
control/              人工变更、审批、备份、终端会话和只读执行边界
rag_engine/           设备识别、证据构造、Prompt、LLM 调用和诊断
knowledge_base/       知识文章、Embedding、Chroma 索引和检索
ops_tools/            只读工具网关
network/              初始设备台账和工具注册数据
webapp/               FastAPI 应用、鉴权、API 和旧版页面
frontend/             React + TypeScript 控制台
scripts/              与 Web 启动解耦的 Linux 运维脚本
docs/                 架构、开发、运维和设计文档
```

数据库访问按领域拆分在 `collector/db/` 下，并由 `collector/db/__init__.py` 统一导出。
调用方继续使用稳定公共函数，不直接依赖 SQLite 表布局。

## 3. Web 分层

Web 层以“应用装配、路由、模板、静态资源”分离为原则：

```text
webapp/
  main.py             ASGI 应用导出和本地运行入口
  app.py              FastAPI 装配、中间件和生命周期
  api/v1/             控制台资源 API
  routers/            控制台页面、旧版页面和旧版 API
  templates/          旧版 Jinja 模板与登录页
  static/             登录页和旧版前端资源
```

当前路由边界：

| 路由 | 用途 |
|---|---|
| `/console/*` | React 控制台，默认业务前端 |
| `/` | 登录后跳转到 `/console/overview` |
| `/legacy` | 旧版 Jinja 总览入口 |
| `/diagnose`、`/changes`、`/knowledge`、`/evaluations` | 保留的旧版业务页面 |
| `/api/v1/*` | 新控制台使用的稳定资源契约 |
| `/api/*` | 旧页面和既有调用方使用的兼容接口 |

旧 URL 不改写，确保切换期间可以逐页迁移。新功能只依赖 `/api/v1`。

## 4. 请求与数据流

1. 启动器检查 Python、Node、依赖、配置和知识库索引。
2. FastAPI 初始化 SQLite；设备表为空时导入初始台账。
3. 浏览器通过单口令登录获得 HttpOnly Cookie。
4. 控制台读取设备、采集、拓扑、诊断、知识和设置 API。
5. 诊断只使用已发布采集批次中的设备事实和知识库检索结果。
6. 长任务立即返回 `202` 和 `job_id`，前端轮询任务状态。
7. 人工确认后才能打开终端；命令、输出和结束原因都写入审计表。
8. 建议修复由人工执行并复测，系统不存在自动下发配置变更的路径。

## 5. 数据权威与证据链

- `devices` 是运行时设备台账的权威来源；JSON 台账只用于首次种子和显式导入导出。
- 一次采集由 `collection_runs` 表示，设备结果、采集项、原始输出和状态事实逐层关联。
- 只有 `published_at_utc` 非空的采集批次可以成为“当前状态”来源。
- 本轮不可达或未采集时返回 `unknown`，不得用历史成功事实补齐。
- 拓扑边缘保留来源状态事实，诊断证据可以回到设备、批次和采集项。
- 知识命中保存文章版本和检索审计，文章后续修改不会改变历史依据。

## 6. 安全边界

- 单口令至少 16 位，支持 Bearer、`X-API-Token` 和 HttpOnly Cookie。
- 自动设备命令必须命中只读白名单。
- 模型只能提出建议，不能直接创建终端会话或下发配置。
- 终端创建必须人工确认备份；写命令在界面中再次确认。
- API Key、设备口令和登录令牌在读取设置时始终掩码。
- `/docs`、`/redoc` 和 OpenAPI 默认关闭，可通过配置显式开启。

## 7. 旧前端策略

旧版页面仍由 FastAPI 和 Jinja 渲染，继续服务诊断、变更、知识、评测等存量流程。
React 控制台是新前端主入口。迁移以“先兼容、再替换单页、最后删除旧路由”为原则，
避免在一次结构调整中同时改变视觉、接口和数据行为。

相关文档：

- [API 契约](API.md)
- [数据库设计](DATABASE_DESIGN.md)
- [前端结构](FRONTEND.md)
- [开发环境](DEVELOPMENT.md)
