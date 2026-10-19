# 数据库结构设计（一期 + 二期）

本文描述企业网络智能运维诊断系统的完整数据模型。一期先把
**资产、采集批次、设备结果、采集项和状态事实**建立成可追溯的数据链；二期继续增加
**诊断会话、模型工具调用、检索审计、证据关联和人工变更事件流**，同时保留旧版
诊断、人工处理和运行状态表，确保现有页面与迁移期流程继续工作。

> 当前实现版本：`schema_migrations.version = 4`。版本 1 为
> `phase1_asset_collection_state`，版本 2 为 `normalize_utc_timestamps`，
> 版本 3 为 `phase2_agent_sessions_tools_and_citations`，
> 版本 4 为 `phase2b_human_change_and_operations`。

## 1. 设计目标

一期解决以下问题：

- 一次采集不再是一组互相独立的“最新命令记录”，而是一个有开始、结束和发布时间的完整批次。
- 批量采集、指定设备采集、人工复测以及未来的 LLM/MCP 工具调用共用同一个采集模型。
- SSH、SNMP、Ping 等原始输出与解析后的状态事实分开保存。
- 接口、路由、VLAN、ACL、邻居、可达性和配置都能成为独立状态事实，为拓扑构建和诊断提供证据。
- 最近一次设备不可达时，当前状态返回 `unknown`，不再沿用历史成功状态。
- 旧 `collection_records` 表保留为兼容视图，迁移期间现有页面和测试不中断。

## 2. 总体数据流

```mermaid
flowchart LR
    TRIGGER["启动 / 手动 / 定时 / LLM工具 / MCP"] --> RUN["collection_runs"]
    RUN --> DCR["device_capture_results"]
    DCR --> ITEM["collection_items"]
    ITEM --> RAW["raw_blobs"]
    ITEM --> OBS["state_observations"]
    OBS --> TOPO["topology_snapshots / nodes / edges"]
    OBS --> CURRENT["当前状态读模型"]
    CURRENT --> RAG["RAG 诊断证据"]
```

核心规则：

1. 每台设备在一次任务中有一条 `device_capture_results`。
2. 每个 SSH 命令、SNMP 轮询或 Ping 是一条 `collection_items`。
3. 每条采集项可以解析出多条 `state_observations`。
4. 只有 `collection_runs.published_at_utc` 非空的任务才会成为当前状态来源。
5. 设备本轮不可达时，当前状态只返回不可达事实；接口、路由等旧成功事实不参与当前诊断。

## 3. 资产、接口与拓扑

```mermaid
erDiagram
    SITES ||--o{ DEVICES : "站点"
    DEVICES ||--o{ DEVICE_ENDPOINTS : "SSH/SNMP 管理入口"
    DEVICES ||--o{ INTERFACES : "稳定接口身份"
    INVENTORY_VERSIONS ||--o{ INVENTORY_ITEMS : "台账版本"
    INVENTORY_ITEMS }o--o| DEVICES : "解析到设备"
    TOPOLOGY_SNAPSHOTS ||--o{ TOPOLOGY_NODES : "拓扑节点"
    TOPOLOGY_SNAPSHOTS ||--o{ TOPOLOGY_EDGES : "拓扑链路"
    DEVICES ||--o{ TOPOLOGY_NODES : "已纳管节点"
    STATE_OBSERVATIONS ||--o{ TOPOLOGY_EDGES : "邻居证据"

    SITES {
        text site_id PK
        text site_code UK
        text name
        text parent_site_id
        text timezone
    }

    DEVICES {
        text device_id PK "稳定设备 ID"
        text device_key UK "台账唯一键"
        text device_name UK "当前显示名"
        text site_id FK
        text role
        text vendor
        text model
        text os_family
        text os_version
        text mgmt_ip
        text device_type
        text lifecycle_status
    }

    DEVICE_ENDPOINTS {
        text endpoint_id PK
        text device_id FK
        text protocol "ssh/snmp/ping"
        text host
        int port
        text credential_ref "只存 Vault 引用"
        int enabled
    }

    INTERFACES {
        text interface_id PK
        text device_id FK
        text if_name
        int if_index
        text media_type
    }

    INVENTORY_VERSIONS {
        text version_id PK
        text source
        text source_hash
        text captured_at_utc
        text imported_by
    }

    INVENTORY_ITEMS {
        text item_id PK
        text version_id FK
        text device_key
        text device_id FK
        text attributes_json
    }

    TOPOLOGY_SNAPSHOTS {
        text snapshot_id PK
        text run_id FK
        text source
        text created_at_utc
        text confidence
    }

    TOPOLOGY_NODES {
        text node_id PK
        text snapshot_id FK
        text device_id FK
        text node_key
        text label
    }

    TOPOLOGY_EDGES {
        text edge_id PK
        text snapshot_id FK
        text a_node_id FK
        text b_node_id FK
        text a_interface_id FK
        text b_interface_id FK
        text link_type
        text status
        text source_observation_id FK
        text confidence
    }
```

台账版本通过 `inventory_versions` 和 `inventory_items` 保留历史。`devices` 只保存
当前设备身份；接口的稳定身份放在 `interfaces`，接口状态则保存在
`state_observations`，二者不混用。

`topology_snapshots` 是某次采集生成的拓扑快照。CDP 解析出的邻居先进入
`state_observations(state_type='neighbor')`，再由拓扑构建器生成节点和链路。
拓扑边保留 `source_observation_id`，可以回到原始采集证据。

## 4. 采集批次与状态事实

```mermaid
erDiagram
    COLLECTION_RUNS ||--o{ DEVICE_CAPTURE_RESULTS : "一次批次"
    DEVICES ||--o{ DEVICE_CAPTURE_RESULTS : "每台设备"
    DEVICE_CAPTURE_RESULTS ||--o{ COLLECTION_ITEMS : "命令/工具结果"
    COLLECTION_ITEMS ||--o{ STATE_OBSERVATIONS : "一条输出多条事实"
    RAW_BLOBS ||--o{ COLLECTION_ITEMS : "原始内容"
    COLLECTION_ITEMS ||--o{ CONFIG_SNAPSHOTS : "运行配置快照"
    COMMAND_POLICIES ||--o{ COLLECTION_ITEMS : "只读命令策略"

    COLLECTION_RUNS {
        text run_id PK
        text trigger_type "startup/manual/llm_tool/mcp/scheduled"
        text scope_type "batch/device/tool/recheck"
        text requested_by
        text source_ref
        text inventory_version_id FK
        text started_at_utc
        text finished_at_utc
        text published_at_utc
        text status "running/success/partial/failed"
        text summary_json
    }

    DEVICE_CAPTURE_RESULTS {
        text result_id PK
        text run_id FK
        text device_id FK
        text result_type "full/recheck/single"
        text reachability "reachable/unreachable/unknown"
        text overall_status "success/partial/failed/skipped"
        text error_code
        text started_at_utc
        text finished_at_utc
        text published_at_utc
    }

    COLLECTION_ITEMS {
        text item_id PK
        text result_id FK
        text device_id FK
        text source "ssh/snmp/ping/api/mcp/manual"
        text capture_type
        text command_or_tool
        text policy_id FK
        text collection_status "success/failed/timeout"
        text observation_status "observed/unreachable/unknown"
        text raw_blob_id FK
        text parsed_json
        text content_hash
    }

    RAW_BLOBS {
        text blob_id PK
        text sha256 UK
        text media_type
        int size_bytes
        text content_text
        text storage_ref
    }

    STATE_OBSERVATIONS {
        text observation_id PK
        text item_id FK
        text device_id FK
        text state_type
        text entity_type
        text entity_key
        text observed_at_utc
        text payload_json
        text confidence
    }

    CONFIG_SNAPSHOTS {
        text config_id PK
        text device_id FK
        text item_id FK
        text blob_id FK
        text sha256
        text captured_at_utc
        int is_running
    }

    COMMAND_POLICIES {
        text policy_id PK
        text device_type
        text operation_key
        text command_template
        int read_only
        int enabled
        text version
    }
```

### 4.1 一次采集的生命周期

1. 调用 `start_collection_run()` 创建 `running` 批次。
2. 每台设备创建一条 `device_capture_results`，初始为 `running/unknown`。
3. SSH、SNMP、Ping 返回后，每条结果写入 `collection_items`，并解析为
   `state_observations`。
4. 设备完成后更新 `reachability` 和 `overall_status`。
5. 全部设备完成后写入 `finished_at_utc`、`published_at_utc` 和批次摘要。
   所有新表时间统一转换为 UTC；旧库迁移时会执行一次时间归一化。
6. 只有已发布批次通过 `get_current_device_bundle()` 进入当前状态和 RAG 证据。

### 4.2 状态类型

`state_observations` 是通用事实表，当前支持：

| `state_type` | 典型 `entity_key` | 内容 |
|---|---|---|
| `reachability` | 目标 IP | 源设备到目标是否可达 |
| `interface` | `Ethernet0/0.10` | admin/oper 状态、IP、ifIndex |
| `route` | `0.0.0.0/0` | 协议、下一跳、出接口、管理距离 |
| `vlan` | `VLAN10` | 名称、状态、端口列表 |
| `acl` | `BLOCK_BRANCH` | ACL 规则顺序和动作 |
| `neighbor` | `cdp:Ethernet0/0:HQ-R1` | CDP/LLDP 邻居和远端接口 |
| `arp` | `10.10.10.100` | MAC、接口 |
| `system` | `sysUpTime` | 在线状态和运行时间 |
| `config` | `running-config` | 配置行数、哈希和摘要 |
| `command_output` | 命令名 | 暂未专门解析的原始输出 |

接口状态优先按 `entity_key` 去重；例如 SSH 的接口 IP 与 SNMP 的
admin/oper 状态可以合并，而不是让后到的 SNMP 结果覆盖掉 SSH 的 IP。

## 5. 当前状态读模型

当前不直接使用 `MAX(collection_items.id)`。当前状态查询规则是：

1. 每台设备选择最近一次已发布的 `full` 或 `single` 结果；
2. 如果没有，再使用最近的 `recheck` 结果；
3. `recheck` 比全量采集更新时，优先使用它的可达性事实；
4. 本轮 `reachability=unreachable` 时，接口、路由等旧成功事实不返回；
5. 没有采集记录的设备返回 `no_data`，不编造状态。

对应函数：

- `get_current_device_bundle(device_name)`：单设备结果、采集项和状态事实。
- `get_current_state(device_names)`：扁平化状态事实，供诊断和工具查询。
- `get_current_device_status()`：设备级可达性、总体状态和最近采集时间。
- `get_all_latest_status()`：兼容旧页面，返回当前批次的采集项。

## 6. 旧表迁移与兼容

首次运行 `python -m collector.migrate` 或启动应用时，`db.init_db()` 会：

1. 将旧数据库备份为 `<db>.pre-phase1-<timestamp>.bak`；
2. 把旧 `devices` 改名为 `devices_legacy`；
3. 把旧 `collection_records` 改名为 `collection_records_legacy`；
4. 创建新资产、采集和状态表；
5. 从旧表生成一个 `trigger_type='migration'` 的采集批次；
6. 修复旧 SSH `parsed_json` 的双重 JSON 编码；
7. 解析旧原始输出为状态事实，并生成拓扑快照；
8. 把所有时间归一化为 UTC，避免 `+08:00` 与 `+00:00` 的字符串排序错误；
9. 创建 `collection_records` 兼容视图。

兼容视图字段：

`id`、`device_name`、`collected_at`、`status`、`fail_reason`、`command`、
`raw_output`、`parsed_json`。

旧 `collection_records_legacy` 和 `devices_legacy` 不会被删除，可用于对照审计。
二期 v4 迁移、诊断会话回填和旧人工记录回填见第 11 节。

## 7. 索引与 SQLite 约定

一期索引包括：

```text
devices(site_id)
interfaces(device_id, if_name)
inventory_items(version_id)
collection_runs(started_at_utc DESC)
collection_runs(published_at_utc DESC)
device_capture_results(device_id, published_at_utc DESC)
device_capture_results(run_id)
collection_items(result_id, started_at_utc)
collection_items(device_id, started_at_utc DESC)
state_observations(device_id, state_type, observed_at_utc DESC)
state_observations(item_id)
topology_edges(snapshot_id)
```

SQLite 约定：

- 每个连接执行 `PRAGMA foreign_keys=ON`；
- 数据库使用 WAL；
- 新表时间统一 UTC；
- 原始输出通过 `raw_blobs.sha256` 去重；
- 大配置后续可把 `raw_blobs.content_text` 迁到对象存储，数据库只保留哈希和引用；
- `schema_migrations` 记录结构版本，后续迁移应按版本追加，不可直接修改旧表。

## 8. 二期诊断会话、上下文与工具审计

二期在采集证据之上增加“会话 -> 消息 -> 上下文 -> 模型调用 -> 检索命中 ->
诊断结论 -> 证据引用”的完整链路。诊断不再只保存一行 `diagnosis_records`，
而是可以通过 `session_id` 恢复用户问题、模型上下文、知识库检索快照和证据关联。

```mermaid
erDiagram
    PROMPT_VERSIONS ||--o{ AGENT_SESSIONS : "Prompt 版本"
    AGENT_SESSIONS ||--o{ AGENT_MESSAGES : "完整消息"
    AGENT_SESSIONS ||--o{ CONTEXT_SNAPSHOTS : "上下文快照"
    AGENT_SESSIONS ||--o{ MODEL_CALLS : "模型调用"
    AGENT_SESSIONS ||--o{ RETRIEVAL_QUERIES : "检索查询"
    RETRIEVAL_QUERIES ||--o{ RETRIEVAL_HITS : "检索命中"
    KB_CHUNKS ||--o{ RETRIEVAL_HITS : "知识片段"
    AGENT_SESSIONS ||--o{ DIAGNOSIS_CONCLUSIONS : "诊断结论"
    DIAGNOSIS_CONCLUSIONS ||--o{ EVIDENCE_LINKS : "证据引用"
    EVIDENCE_LINKS }o--o| STATE_OBSERVATIONS : "设备状态事实"
    EVIDENCE_LINKS }o--o| COLLECTION_ITEMS : "采集项"
    EVIDENCE_LINKS }o--o| RETRIEVAL_HITS : "知识片段"
    TOOL_REGISTRY ||--o{ TOOL_CALLS : "工具定义"
    AGENT_MESSAGES ||--o{ TOOL_CALLS : "消息触发"
    TOOL_CALLS ||--|| TOOL_CALL_RESULTS : "工具结果"
    TOOL_CALL_RESULTS }o--o| COLLECTION_RUNS : "工具触发采集"

    AGENT_SESSIONS {
        text session_id PK
        text title
        text created_by
        text model_id
        text prompt_version_id FK
        text status "active/completed/failed/archived"
        text started_at_utc
        text updated_at_utc
        text ended_at_utc
    }

    AGENT_MESSAGES {
        text message_id PK
        text session_id FK
        int seq_no
        text role "user/assistant/tool/system"
        text content_text
        text content_json
        text created_at_utc
    }

    CONTEXT_SNAPSHOTS {
        text snapshot_id PK
        text session_id FK
        text message_id FK
        text prompt_version_id FK
        text rendered_messages_json
        text rendered_prompt_redacted
        text prompt_hash
        int token_count
    }

    PROMPT_VERSIONS {
        text prompt_version_id PK
        text template_id
        text system_prompt
        text user_template
        text content_hash
        int is_active
    }

    MODEL_CALLS {
        text call_id PK
        text session_id FK
        text message_id FK
        text context_snapshot_id FK
        text provider
        text model
        text request_id
        text params_json
        int input_tokens
        int output_tokens
        int latency_ms
        text status
        text error_code
        text response_blob_id FK
    }

    RETRIEVAL_QUERIES {
        text query_id PK
        text session_id FK
        text message_id FK
        text query_text
        text filters_json
        int top_k
        text status
        text error_message
    }

    RETRIEVAL_HITS {
        text hit_id PK
        text query_id FK
        text chunk_id FK
        int rank_no
        real distance
        text doc_id
        text title
        text source
        text version
        text content
        text content_hash
    }

    KB_CHUNKS {
        text chunk_id PK
        text index_name
        text doc_id
        text title
        text category
        text applicable_device
        text version
        text source
        text content
        text content_hash
    }

    TOOL_REGISTRY {
        text tool_id PK
        text tool_name
        text transport "local/http/mcp"
        text server_name
        text input_schema_json
        int read_only
        int requires_approval
        int enabled
        text version
    }

    TOOL_CALLS {
        text tool_call_id PK
        text session_id FK
        text message_id FK
        text tool_id FK
        text arguments_json
        text status "requested/running/success/failed"
        text error_code
        text error_message
        text started_at_utc
        text finished_at_utc
    }

    TOOL_CALL_RESULTS {
        text result_id PK
        text tool_call_id FK
        text result_kind
        text collection_run_id FK
        text result_json
        text blob_id FK
    }

    DIAGNOSIS_CONCLUSIONS {
        text conclusion_id PK
        int diagnosis_id UK
        text session_id FK
        text final_message_id FK
        text fault_phenomenon
        text root_cause
        text missing_information
        text confidence
    }

    EVIDENCE_LINKS {
        text link_id PK
        text conclusion_id FK
        text link_type "device_evidence/knowledge/tool_result"
        text observation_id FK
        text item_id FK
        text retrieval_hit_id FK
        text tool_call_result_id FK
        text claim
        text role "supporting/conflicting/context/missing"
    }
```

二期工具注册表当前由 `network/tool_registry.json` 初始化，内置：

- `query_device_state`：查询已发布采集快照中的设备状态；
- `query_collection_history`：查询单设备采集项历史；
- `run_readonly_collection`：对指定设备执行白名单内的只读采集。

所有工具调用都会写入 `tool_calls` 和 `tool_call_results`。触发性采集通过
`collection_runs.source_ref = tool_call_id` 与工具调用关联，因此可以从模型请求
回到设备命令、原始输出和状态事实。

`diagnosis_records` 继续保留为兼容表，并新增：

`session_id`、`conclusion_id`、`retrieval_query_id`、`context_snapshot_id`、
`model_call_id`、`kb_status`、`total_response_time_ms`。

历史 `diagnosis_records` 在升级时自动回填为二期的 session/message/conclusion
结构；旧记录不会删除。

## 9. 二期人工变更、物理操作与复测

人工处理不再是一条可被覆盖的 `manual_actions` 记录，而是“变更请求 + 计划步骤 +
审批 + 不可变事件 + 实际命令 + 物理操作 + 配置备份 + 复测”的组合。

```mermaid
erDiagram
    DIAGNOSIS_CONCLUSIONS ||--o{ CHANGE_REQUESTS : "诊断产生变更"
    CHANGE_REQUESTS ||--o{ CHANGE_STEPS : "计划步骤"
    CHANGE_REQUESTS ||--o{ CHANGE_APPROVALS : "审批"
    CHANGE_REQUESTS ||--o{ CHANGE_EVENTS : "不可变事件"
    CHANGE_STEPS ||--o{ COMMAND_EXECUTIONS : "实际命令"
    CHANGE_STEPS ||--o{ PHYSICAL_ACTIONS : "物理操作"
    CHANGE_REQUESTS ||--o{ CONFIG_BACKUPS : "配置备份"
    CHANGE_REQUESTS ||--o{ RECHECKS : "复测"
    RECHECKS }o--o| COLLECTION_RUNS : "复测采集批次"
    CHANGE_REQUESTS ||--o{ ATTACHMENTS : "现场证据"

    CHANGE_REQUESTS {
        text change_id PK
        text conclusion_id FK
        int diagnosis_id FK
        text session_id FK
        text device_id FK
        text device_name
        text change_type "physical/software/mixed"
        text title
        text description
        text risk_level "low/medium/high"
        text status "draft/pending_approval/approved/executing/completed/failed/rolled_back"
        text requested_by
        text approved_by
        text planned_at_utc
        text started_at_utc
        text finished_at_utc
        text rollback_plan
        int legacy_action_id UK
    }

    CHANGE_STEPS {
        text step_id PK
        text change_id FK
        int seq_no
        text step_type "physical/software/verification"
        text source_type "llm/human"
        text title
        text command_text
        text expected_result
        int requires_approval
        text status "pending/ready/running/success/failed/skipped"
        text executed_by
        text started_at_utc
        text finished_at_utc
        text result_summary
    }

    CHANGE_APPROVALS {
        text approval_id PK
        text change_id FK
        text approver
        text decision "approved/rejected/changes_requested"
        text comment
        text created_at_utc
    }

    CHANGE_EVENTS {
        text event_id PK
        text change_id FK
        text event_type
        text actor
        text from_status
        text to_status
        text payload_json
        text payload_hash
        text created_at_utc
    }

    COMMAND_EXECUTIONS {
        text execution_id PK
        text step_id FK
        text device_id FK
        text command_text
        text execution_mode "manual/readonly/approved_automation"
        text executed_by
        text started_at_utc
        text finished_at_utc
        int exit_code
        text stdout_blob_id FK
        text stderr_blob_id FK
        text result_summary
        text approval_id FK
    }

    PHYSICAL_ACTIONS {
        text action_id PK
        text step_id FK
        text device_id FK
        text action_type "cable/power/console/module/other"
        text description
        text performed_by
        text performed_at_utc
        text evidence_blob_id FK
        text result
    }

    CONFIG_BACKUPS {
        text backup_id PK
        text change_id FK
        text device_id FK
        text config_snapshot_id FK
        text blob_id FK
        text sha256
        text captured_at_utc
        text backup_type "before/after/rollback"
        int verified
        text storage_ref
    }

    RECHECKS {
        text recheck_id PK
        text change_id FK
        text collection_run_id FK
        text source_device_id FK
        text source_device_name
        text target
        text check_type "ping/interface/route/acl/application"
        text expected_json
        text actual_json
        text pass_fail "pass/fail/inconclusive"
        text evidence_observation_id FK
        text executed_at_utc
    }

    ATTACHMENTS {
        text attachment_id PK
        text owner_type "change/step/physical_action/report"
        text owner_id
        text blob_id FK
        text mime_type
        text caption
        text uploaded_by
    }
```

### 9.1 推荐状态流转

```text
draft
  -> pending_approval
  -> approved
  -> executing
  -> completed

pending_approval -> rejected
approved/executing -> failed
approved/executing -> rolled_back
```

规则：

1. 创建变更时，`change_type`、风险级别、目标设备和关联诊断必须明确。
2. 审批只追加 `change_approvals` 和 `change_events`，不回写历史审批。
3. 配置备份在审批后执行；备份内容和哈希进入 `raw_blobs` 与 `config_backups`。
4. 软件命令必须由人工执行后录入 `command_executions`；系统不自动下发变更。
5. 物理操作进入 `physical_actions`，可把现场说明或照片哈希关联到 `attachments`。
6. 复测必须创建真实 `collection_runs`，并从 `state_observations` 取复测证据。
7. `change_events` 只追加，不更新或删除；`change_requests.status` 是当前状态缓存。

### 9.2 物理操作与软件命令的统一性

`change_steps` 用 `step_type` 区分：

- `physical`：拔插线缆、断电、Console、模块操作；
- `software`：人工执行或准备执行的命令；
- `verification`：验证步骤，可绑定 `rechecks`。

`source_type` 区分 `llm` 和 `human`。LLM 生成的是计划建议；人工修改后的实际命令
进入 `command_executions.command_text`，两者不会混为一条文本。

## 10. 运行健康、报告和审计

二期增加：

- `health_checks`：记录组件、状态、检查时间和详细信息；
- `audit_log`：记录操作者、动作、目标对象和请求 ID；
- `reports`：记录报告文件哈希、所属会话、诊断结论或变更；
- `attachments`：保存物理操作、报告等对象的附件引用。

这些表不替代 `system_status`。`system_status` 仍是当前启动快照，
`health_checks` 才是可查询的历史检查记录。应用启动会写入 `database`、
`knowledge_base` 和 `runtime` 三条健康检查；`GET /api/health` 返回当前快照
和最近检查记录。

## 11. 二期迁移与兼容

`db.init_db()` 在版本低于 4 时执行：

1. 创建二期诊断、工具、检索和人工变更表；
2. 为旧 `diagnosis_records` 增加兼容列；
3. 把旧诊断记录回填为 session/message/conclusion；
4. 把旧 `manual_actions` 回填为历史 `change_requests`、步骤、备份和复测；
5. 把旧 SSH `parsed_json` 的双重编码在采集迁移阶段修复；
6. 写入 `schema_migrations` 版本 4。

升级前自动创建 `<db>.pre-v4-<timestamp>.bak`。迁移不删除旧表：
`devices_legacy`、`collection_records_legacy`、旧 `diagnosis_records` 和
`manual_actions` 都保留用于对照审计。

## 12. 索引与后续阶段

二期新增索引重点覆盖：

```text
agent_messages(session_id, seq_no)
context_snapshots(session_id, created_at_utc DESC)
tool_calls(session_id, started_at_utc DESC)
model_calls(session_id, created_at_utc DESC)
retrieval_hits(query_id, rank_no)
change_requests(device_id, status, updated_at_utc DESC)
change_steps(change_id, seq_no)
change_events(change_id, created_at_utc)
command_executions(step_id, created_at_utc DESC)
rechecks(change_id, executed_at_utc DESC)
```

后续仍建议增加：

- `test_suites`、`test_cases`、`fault_injections`、`experiment_runs`、
  `experiment_results`：故障注入和知识库对比评测；
- 知识文章版本、来源 URL、适用平台和嵌入模型索引版本；
- 用户、角色、审批权限和密钥 Vault 引用；
- 生命周期归档、原始输出保留期限和对象存储；
- 多用户并发写时的 PostgreSQL/JSONB 迁移。
