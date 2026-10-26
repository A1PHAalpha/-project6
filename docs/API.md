# API 约定

## 1. 鉴权

除 `/login`、`/logout` 和 `/static/*` 外，请求都需要认证。支持三种方式：

```http
Authorization: Bearer <APP_API_TOKEN>
X-API-Token: <APP_API_TOKEN>
Cookie: netops_token=<APP_API_TOKEN>
```

浏览器登录成功后获得 HttpOnly Cookie。页面请求未认证时跳转 `/login`，API 请求返回
`401`：

```json
{"error": "未授权"}
```

## 2. 通用约定

- 新控制台只使用 `/api/v1`；`/api` 为旧版兼容接口。
- 列表统一返回 `items`、`total`、`page`、`page_size`。
- 长任务返回 `202` 和 `job_id`，通过任务接口查询进度。
- 时间使用 UTC ISO 8601。
- 未采集使用 `unknown`，不转换为 `unreachable`。
- 设备状态必须能追溯到采集批次。

资源错误通常返回：

```json
{
  "code": "not_found",
  "message": "资源不存在",
  "request_id": "..."
}
```

## 3. v1 资源

### 总览与设备

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/overview` | 总览指标、拓扑、最近任务和会话 |
| GET | `/api/v1/devices` | 设备台账，支持分页和筛选 |
| POST | `/api/v1/devices` | 新建设备 |
| PATCH | `/api/v1/devices/{device_id}` | 修改设备 |
| POST | `/api/v1/devices/archive` | 软退役设备 |
| GET | `/api/v1/devices/{device_id}/current-state` | 当前设备状态与事实 |
| GET | `/api/v1/inventory/export` | 导出设备台账 |
| POST | `/api/v1/inventory/import` | 导入设备台账 |
| GET | `/api/v1/topology` | 当前拓扑快照 |

### 采集与任务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/collection-runs` | 采集批次列表 |
| GET | `/api/v1/collection-runs/{run_id}` | 采集批次详情 |
| POST | `/api/v1/collection-runs` | 创建采集任务 |
| GET | `/api/v1/collection-schedule` | 读取采集计划 |
| PUT | `/api/v1/collection-schedule` | 更新采集计划 |
| GET | `/api/v1/jobs/{job_id}` | 查询后台任务状态 |

### 知识库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/knowledge-base/status` | 索引、文章和模型状态 |
| GET | `/api/v1/knowledge-base/articles` | 文章列表 |
| POST | `/api/v1/knowledge-base/rebuild` | 创建增量或全量构建任务 |

### 诊断与会话

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/diagnoses` | 诊断记录 |
| GET | `/api/v1/sessions` | 诊断会话列表 |
| GET | `/api/v1/sessions/{session_id}` | 会话、证据和消息 |
| GET | `/api/v1/sessions/{session_id}/report` | 导出 Markdown 报告；加 `?format=html` 导出可打印 HTML |
| POST | `/api/v1/diagnose` | 创建诊断 |

### 设置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/settings/environment` | 读取掩码后的设置 |
| PUT | `/api/v1/settings/environment` | 原子更新白名单配置 |
| POST | `/api/v1/settings/llm/test` | 测试 LLM 连接 |
| POST | `/api/v1/settings/embedding/test` | 测试 Embedding 模型 |

### 人工终端

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/terminal/sessions` | 创建人工终端会话 |
| GET | `/api/v1/terminal/sessions/{session_id}` | 读取会话 |
| POST | `/api/v1/terminal/sessions/{session_id}/commands` | 执行命令并审计 |
| POST | `/api/v1/terminal/sessions/{session_id}/write-intents` | 为写命令签发一次性确认令牌 |
| POST | `/api/v1/terminal/sessions/{session_id}/backup` | 备份当前配置 |
| POST | `/api/v1/terminal/sessions/{session_id}/close` | 关闭会话 |
| GET | `/api/v1/terminal/history/{device_name}` | 终端历史 |

创建终端必须提交 `backup_acknowledged=true`。快速操作还必须提交 `confirmed=true`。
写命令必须在 `backup_status=completed` 后调用 `write-intents` 获取一次性
`confirmation_id`，再随命令提交；令牌绑定会话、命令哈希并有短有效期。

## 4. 旧版兼容接口

旧页面继续使用 `/api`：

```text
POST /api/diagnose
GET  /api/sessions
GET  /api/evaluations
GET  /api/audit
POST /api/changes
POST /api/changes/{change_id}/approve
POST /api/changes/{change_id}/backup
POST /api/changes/{change_id}/recheck
GET  /api/export/{diagnosis_id}
GET  /api/health
```

完整路由以应用和路由测试为准。新增前端功能不得继续扩展 `/api` 兼容层。

## 5. 接口调试

默认关闭 OpenAPI 页面。本地需要 Swagger 时，在 `.env` 中设置：

```dotenv
APP_ENABLE_DOCS=true
```

重启后访问 `/docs`。该页面不绕过鉴权，生产环境应保持关闭。
