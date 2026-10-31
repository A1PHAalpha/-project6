# 项目需求核对报告

- 核对日期：2026-10-31
- 核对对象：`/home/wyj/project6`
- 核对方式：阅读 README、验收文档、源代码和测试；查询 `data/ops.db` 的采集/诊断/审计记录；运行后端测试、静态检查、前端测试与构建；实际调用本地知识库检索（未调用外部大模型）
- 核对原则：以当前目录中可复核的代码、数据库记录和报告为准，不以 `docs/ACCEPTANCE_REPORT.md` 的结论替代证据

## 总体结论

**当前项目不能判定为“8 项需求全部逐条完成”。**

需求 1、2、4、6 的核心能力已经落地，需求 3、5、8 基本落地但存在明确质量/证据缺口；**需求 7 的“4 类故障真实设置 + 每类 2 个独立案例 + 可核查的 20 次对比结果”没有在真实实验网上完成，且评测结果没有保留在当前数据库中**，是本项目最主要的未完成项。

| 需求 | 核对结论 | 简要说明 |
|---|---|---|
| 1. 实验网络 | 基本完成（有保留） | 3 路由器/3 交换机/3 终端、SSH/SNMP、VLAN/路由/ACL 均有运行证据；但 EVE-NG/ZeroTier 实验网和启动配置不在仓库内，无法从本目录重建 |
| 2. 状态采集 | 完成 | Python SSH/SNMP/Ping 采集，保留设备名、时间、原始证据；不可达不沿用历史成功状态；实测 39/39 SSH、6/6 SNMP、517 条观测/10 类 |
| 3. 运维知识库 | 基本完成（有质量缺陷） | 26 篇，覆盖 VLAN/路由/DHCP/ACL/接口故障；来源、适用设备、版本字段齐全；但 26/26 没有可核验 URL，且关键案例检索存在偏差 |
| 4. 大模型接入/RAG | 完成 | 真实 `qwen-plus` 调用成功；模型、模板、参数、检索命中、上下文快照均有记录；Prompt 未发现明文凭据 |
| 5. 故障诊断 | 部分完成 | 6 个必需字段齐全、证据 ID 可追溯；但“正常网络”诊断编造了“默认路由配置缺失”，P5 复测目标错误，建议命令为多段命令 |
| 6. 操作控制 | 完成 | 自动检查只读白名单；写命令必须完成备份 + 一次性令牌；终端事件、报告导出有审计；脱敏测试通过；P5 未走正式变更单，但走审计终端 + 备份 + 复测 |
| 7. 效果测试 | **未完全完成** | 10 个用例和对比框架存在，但证据是 `seed_records` 预置文本，不是真实故障注入；4 类故障没有各 2 次真实演练；20 次评测结果未保留在当前库 |
| 8. 结果展示 | 基本完成 | React 控制台 + 旧版页面 + Markdown/HTML 报告；P5 有真实“故障注入→采集→诊断→备份→修复→复测”记录；但 `demo_e2e.py` 是模拟脚本，不能作为现场证据 |

---

## 1. 实验网络（需求 1）

### 核对结果：基本完成（有保留）

已核对到的证据：

- `network/device_inventory.json` 登记 9 台设备：3 台 IOSv 路由器（HQ-R1、BR-A-R2、BR-B-R3）、3 台 IOSvL2 交换机（HQ-SW1、BR-A-SW2、BR-B-SW3）、3 台测试终端（HQ-SRV、BR-A-PC、BR-B-PC）。
- `data/ops.db` 的 `devices` 表也是 9 台，站点为总部、分支A、分支B，均带管理 IP 和设备类型。
- `collection_runs` 中的全量批次 `run_028bbce44fcc4d7285bdd120722e2702`：
  - 设备数 9；
  - SSH 命令成功/失败 39/0；
  - SNMP 设备成功/失败 6/6；
  - 连通性测试成功/失败 3/0；
  - 该批次产生 517 条 `state_observations`，覆盖 10 类状态。
- 配置快照可复核：
  - HQ-R1 存在 `Ethernet0/0.10/.20/.99` 子接口、`10.10.10.0/24` 等地址、`172.16.1.0/30`/`172.16.2.0/30` 地址、`ip route 10.20.0.0/16`/`10.30.0.0/16` 以及 `WAN-IN` ACL；
  - HQ-SW1 存在 VLAN 10/20/99/999、trunk 和 SVI 配置；
  - BR-A-R2 存在 `10.20.10.1/.20.1/.99.1` 子接口、默认路由 `0.0.0.0/0 172.16.1.1`。
- `pytest` 中 `test_connectivity_semantics.py` 覆盖了“ping 命令执行成功但目标不可达”不算业务成功；`test_collector_commands.py` 覆盖了路由器/交换机只读命令差异。

### 保留意见

1. **实验网不在本目录内**：仓库只有设备台账、采集结果和文档说明，没有 EVE-NG 拓扑文件、路由器/交换机启动配置或可一键重建实验网的脚本。`docs/NETWORK_CONNECTION_GUIDE.md` 说明实验网是外部 EVE-NG + ZeroTier；因此本目录可以被视为“运行证据”，但不能独立重建实验网络。
2. **“5 条业务路径”不可完全复核**：`docs/ACCEPTANCE_REPORT.md` 写“人工补充的 5 条跨站路径均可达”，但当前 `data/ops.db` 全量批次只有 3 条 `reachability` 观测（3 个终端各 1 条）。3/3 成功足以证明基本业务，但报告中的 5 条没有对应的可查询记录。
3. 终端是 VPCS，没有 SSH；采集通过 HQ-R1 作为 ping 源，并在 `parsed.execution_location=source_router` 中注明，这一点是透明的，不构成造假。

**结论：功能层面通过，交付可复现性不足。**

---

## 2. 状态采集（需求 2）

### 核对结果：完成

实现链路：

- `collector/collector_ssh.py`：Netmiko 执行只读命令集合，失败时记录 `status=failed`、`error_code`、`fail_reason`，成功/失败都保留原始输出或错误。
- `collector/collector_snmp.py`：SNMP `sysUpTime` + `ifTable`，返回在线状态和接口 admin/oper 状态。
- `collector/connectivity_test.py`：支持从源路由器/源终端 ping，并记录 `execution_location`。
- `collector/run_collect.py`：统一创建 `collection_runs -> device_capture_results -> collection_items`，设备不可达时 `overall_status=failed`，业务 ping 不可达也计入失败。
- `rag_engine/evidence_builder.py`：只取已发布的最新采集结果；最新结果不可达时返回设备级不可达事实，丢弃历史接口状态。
- `collector/db/collection.py`：`collection_items` 保留 `raw_blob_id`/`error_message`；`state_observations` 有 `observed_at_utc`。

实测证据：

- 全量批次 517 条观测、10 类状态；每台网络设备 7～8 类状态。
- `state_observations.observed_at_utc` 空值 0；`collection_items` 中原始证据/错误同时缺失的记录 0。
- `tests/test_db_phase1.py::test_failed_latest_capture_does_not_reuse_old_interface_state` 明确验证：最新采集失败后，`build_evidence_for_devices` 只返回 `device_reachability`，不返回旧的接口状态。
- `tests/test_connectivity_semantics.py` 验证业务不可达不被记为连通性成功。

### 保留意见

1. 采集项包括接口、VLAN、路由、配置快照、ACL、邻居、ARP、系统信息；没有单独采集 `show logging`。需求原文是“关键配置**或**日志”，配置已覆盖，因此不判失败；如果验收要求“日志”作为独立证据，则需补充。
2. 对不可达设备的设备级证据使用 `observation_id=null`（例如 P5 的 BR-A-PC），只有 `run_id`、设备名和采集时间。设备和批次可追溯，但不满足“所有 evidence_used.observation_id 都非空”这种更严格的口径；验收标准 D2 的“100% observation_id 存在于本次证据”需要放宽解释。
3. 评测场景仍使用 `seed_records` 预置文本（见需求 7），但这是评测路径，不是生产采集路径；生产采集本身是真实 SSH/SNMP。

**结论：通过。**

---

## 3. 运维知识库（需求 3）

### 核对结果：基本完成（有质量缺陷）

实测：

- `knowledge_base/articles.json` 共 26 篇：
  - VLAN 5、路由 5、DHCP 4、ACL 5、接口故障 5、通用排查方法 2。
- 26 篇的 `source`、`applicable_device`、`version` 字段均非空。
- 10 个评测场景的完整问题、`expected_notes` 与知识库正文做字符串匹配，未发现直接写入；K5“测试答案隔离”成立。
- 固定 6 个领域查询的 Top 3 类别命中为 6/6：
  - “分支A路由器和交换机之间不通” → 接口故障；
  - “接口 line protocol down” → 接口故障；
  - “VLAN 10 不通” → VLAN；
  - “OSPF 邻居起不来” → 路由；
  - “ACL 拦截业务流量” → ACL；
  - “DHCP 地址池耗尽” → DHCP。

### 质量缺陷

1. **来源不可核验**：26/26 篇文章没有 `source_url`；`source` 多为“Cisco官方文档《…》某章节”或“Cisco社区知识库…专题”，没有链接、文档编号或具体版本页码。字段形式上齐全，但不满足严格的“来源可追溯”。
2. **版本与实验网不完全一致**：知识库版本写的是 `IOS 15.x (GNS3)`，实验网是 EVE-NG 的 IOSv/IOSvL2；虽然命令体系接近，但“与实验设备相符”只能算基本相符。
3. **P5 实际检索命中偏差**：对真实问题“分支A的终端无法访问总部服务器了，请帮我诊断原因。”当前检索 Top 3 是：
   - `KB-ROUTE-001`（缺少默认路由）
   - `KB-DHCP-001`（DHCP 中继）
   - `KB-IF-004`（err-disabled）
   
   最贴近“接口 administratively down”的 `KB-IF-001` 没有进入 Top 3；模型最终引用了 `KB-IF-004`，但 P5 的真实原因是 `shutdown` 导致 administratively down，不是 err-disabled。检索类别命中与“具体根因命中”不是一回事。
4. 知识库没有文章编辑/导入接口，只有查看和索引重建；需求只要求整理和检索，因此不判失败，但作为“运维知识库”治理能力不完整。

**结论：数量、分类、字段、检索流程通过；来源可核验性和案例级检索质量需要补强。**

---

## 4. 大模型接入与 RAG（需求 4）

### 核对结果：完成

可复核证据：

- `model_calls` 有 2 次真实调用，模型均为 `qwen-plus`，`status=success`，包含 provider、request_id、token 用量、latency。
- `diagnosis_records` 保存：
  - `model_id`；
  - `prompt_template_id=diagnosis_v2`；
  - `llm_params_json`（temperature=0.2、max_tokens=3000）；
  - `retrieved_doc_ids_json`；
  - `context_snapshot_id`；
  - `response_time_ms` 和 `total_response_time_ms`。
- `prompt_versions` 保存 system prompt 和 user template；`context_snapshots` 保存渲染后的 prompt/脱敏 prompt。
- `retrieval_queries` / `retrieval_hits` 保存 query、top_k、命中文章、排名、距离、版本、来源和内容哈希。
- 诊断流程顺序正确：`diagnose()` 先识别设备 → 读取当前证据 → 检索知识库 → 组装 prompt → 调用模型 → 结构化解析 → 落盘。
- 对 `context_snapshots` 做敏感词扫描，未发现未脱敏的 `password`、`secret`、`community`、`username ... password/secret`。
- 配置发给外部模型时，`evidence_builder._summarize_observation()` 对 `config` 只输出行数和 sha256，不输出完整 running-config；证据摘要再经过 `sanitize_evidence_list()`。

**结论：通过。**

---

## 5. 故障诊断（需求 5）

### 核对结果：部分完成

已满足的部分：

- P5 回答和正常网络回答都包含 6 个字段：`fault_phenomenon`、`evidence_used`、`possible_causes`、`check_steps`、`fix_suggestions`、`recheck_method`，另有 `missing_information`。
- `evidence_used` 中带 ID 的条目都能在本次 `evidence_json` 中找到；设备名和采集时间也存在。
- 证据不足时 `missing_information` 非空；P5 明确说明 HQ-R1/HQ-SRV/HQ-SW1 没有采集数据，没有凭空给出这些设备的接口状态。
- 知识库开启时，`possible_causes` 会带 `kb_refs`；P5 引用 `KB-IF-004`、`KB-ROUTE-001`。

### 明确缺陷

1. **正常网络诊断编造了设备状态（最重要）**
   - `diagnosis_records.id=2` 的问题是“帮我检查一下是否存在问题”，回答的 `fault_phenomenon` 是“未发现明确故障现象”，但 `missing_information` 写：
     > “BR-A-R2、BR-B-R3 等路由器的默认路由（Gateway of last resort）配置缺失，无法判断是否具备访问未知网段能力……”
   - 实际全量采集中，BR-A-R2 和 BR-B-R3 都有 `0.0.0.0/0` 默认路由（分别指向 172.16.1.1 和 172.16.2.1）。
   - 这是把“本次证据选择没有包含默认路由”误写成“设备配置缺失”，属于需求 5 明确禁止的“编造设备状态”。根因是 `evidence_builder.select_compact_evidence()` 的路由配额只保留 4 条/设备，默认路由被其他路由挤出；模型没有区分“缺少证据”和“缺少配置”。
2. **P5 复测目标错误**
   - `reports/diagnosis_1.md` 第 137 行写：
     > “在 BR-A-R2 上执行 ping 192.168.249.12（总部服务器IP）……”
   - `192.168.249.12` 实际是 BR-A-R2 自己的管理 IP；总部服务器 HQ-SRV 的地址是 `10.10.10.100`（见 `network/device_inventory.json`）。复测方法因此不可执行。
   - 人工实际复测使用的是 `HQ-R1 -> 10.20.10.11`，这是正确的；报告里的 AI 复测建议没有经过人工修正就入库。
3. **处理建议命令不是单条可执行命令**
   - P5 的 `fix_suggestions[0].command` 为：
     ```text
     interface Ethernet0/0.10; no shutdown; interface Ethernet0/0.20; no shutdown; interface Ethernet0/0.99; no shutdown
     ```
   - Prompt 明确要求 `command` 是单条命令，不能多行/多条拼接；模型违反了该约束，后端也没有校验。
   - 实际人工修复执行的是父接口 `interface Ethernet0/0` + `no shutdown`（见 `terminal_events`），而不是 AI 给出的三个子接口命令。说明 AI 的处理建议与真实修复动作不一致。
4. **知识引用不准确**
   - P5 主因是 `shutdown` 导致的 administratively down，最相关的是 `KB-IF-001`；实际引用的是 `KB-IF-004`（err-disabled），语义不符。
5. **证据引用严格性**
   - P5 的 BR-A-PC 证据 `observation_id=null`，只有设备名、采集时间和 `run_id`；设备/批次可追溯，但严格按“evidence_used.observation_id 必须存在”的验收口径不算完全通过。

**结论：字段完整性和基本证据链通过；但“不编造设备状态”和“处理/复测建议可执行”存在明确失败，不能判定完全通过。**

---

## 6. 操作控制与安全（需求 6）

### 核对结果：完成

已核对：

- `rag_engine` 不导入 `control.terminal_service`、`control.readonly_executor` 或其他执行器；模型输出只进入 `diagnosis_records`，没有“模型 → 配置下发”的代码路径。
- `control/readonly_executor.py` 只执行 `collector/whitelist_commands.py` 中的只读命令；非白名单命令直接抛 `CommandNotWhitelistedError`。
- `control/write_policy.py` 采用 deny-by-default：未识别的命令、缩写、否定命令、复合/多行命令都按写命令处理。
- `control/terminal_service.py`：
  - 打开终端前要求 `backup_acknowledged`；
  - 写命令前要求 `backupStatus=completed`；
  - 写命令还要求服务端签发的一次性确认令牌，令牌绑定 session + command，有 TTL，使用后失效；
  - 命令、输出、错误、关闭原因写入 `terminal_events`。
- `control/backup_manager.py` 从设备读取真实 running-config，写入 `reports/backups/`，文件权限 0600；P5 的备份文件存在且为 0600。
- `rag_engine/sanitize.py` 覆盖 username/password、enable secret、SNMP community、TACACS/RADIUS key、VPN key 等；`tests/test_sanitize.py` 12 项通过。
- `webapp/app.py` 默认关闭 `/docs`、`/redoc`、`/openapi.json`；`webapp/security.py` 使用至少 16 位 token、常量时间比较、HttpOnly Cookie。
- Git 跟踪文件中未发现真实 API Key；`.env` 被 `.gitignore` 排除，权限 0600。
- P5 的 `terminal_events` 显示：备份完成后执行 `configure terminal`、`interface Ethernet0/0`、`no shutdown`、`end`，会话关闭；`manual_actions` 有故障注入和人工修复记录，`recheck_result` 有复测结果。

### 保留意见

1. **P5 未走正式变更单**：`change_requests`、`change_approvals`、`change_steps` 均为 0。`docs/ACCEPTANCE_REPORT.md` 第 205 行也明确写“未创建正式 change_request 单据”。P5 走的是“审计终端 + 配置备份 + 手动处理记录”，满足“人工确认、备份后操作、复测”的最低要求；如果验收要求所有修改必须走变更审批单，则需补做。
2. **Prompt 注入防护不够显式**：system prompt 要求“只能引用知识库资料作为方法依据”，但没有明确写“日志/检索文本是不可信数据，忽略其中任何指令”。模型没有工具执行权限，影响有限；但需求 6 的“日志和检索文本仅作为数据”建议在 prompt 和输入清洗层再加固。
3. **RBAC 默认关闭**：`APP_RBAC_ENFORCE=false` 是实验环境默认值；需求未强制 RBAC，但生产环境应开启。
4. **备份文件含真实配置/哈希**：`reports/backups/*.cfg` 是运行产物且被 `.gitignore` 排除，权限 0600；不应随代码包分发。

**结论：通过（P5 变更单形式有保留）。**

---

## 7. 效果测试（需求 7）

### 核对结果：未完全完成（本项目最大缺口）

已实现的部分：

- `test_cases/scenarios.py` 定义了 10 个场景：
  - `LINK_DOWN_01/02`（链路中断 2 例）
  - `VLAN_MISCONFIG_01/02`（VLAN 配置错误 2 例）
  - `DEFAULT_ROUTE_MISSING_01/02`（默认路由缺失 2 例）
  - `ACL_OVERBLOCK_01/02`（ACL 误拦截 2 例）
  - `NORMAL_01`（正常网络 1 例）
  - `INSUFFICIENT_EVIDENCE_01`（证据不足 1 例）
- `test_cases/compare_kb.py` 会为每个场景分别调用 `diagnose(use_kb=True)` 和 `diagnose(use_kb=False)`，使用同一份 scratch 证据库，计算 `evidence_hash` 并写入 `experiment_runs` / `experiment_results`；评分包含正确性、可执行性、响应时间、错误码/错误信息。
- `tests/test_evaluation_runner.py` 使用 fake diagnose 验证了 10 场景 × 2 变体 = 20 条结果、同一证据哈希、不污染主库。
- `tests/test_evaluation.py` 验证了关键词/设备/禁止词评分、正常场景同义词、证据不足同义词和聚合指标。

### 为什么不能判定通过

1. **4 类故障没有真实“设置”**
   - `scenarios.py` 顶部注释明确写：所有证据通过 `seed_records` 直接写入数据库，模拟“已采集到的真实状态”。
   - 也就是说，10 个案例是预置文本夹具，不是通过 EVE-NG/设备命令真实注入的 4 类故障。
   - `data/ops.db` 的 `fault_injections` 表为 0；`docs/ROADMAP.md` 第 27 行明确把“为真实 EVE-NG 故障注入提供独立执行适配器”列为**尚未完成**的生产硬化项。
   - 真实网络只完成了 P5 的“链路中断”一次演练；VLAN 错误、默认路由缺失、ACL 误拦截没有各 2 次真实注入记录。
2. **测试场景与真实实验设备不一致**
   - 场景使用 `192.168.100.x`、`192.168.20.x`、`192.168.200.x`、`192.168.10.x` 等地址；
   - 真实台账是 `10.10.10.100`（HQ-SRV）、`10.20.10.11`（BR-A-PC）、`10.30.10.11`（BR-B-PC），管理网是 `192.168.249.x`；
   - 场景中的 `GigabitEthernet`、`HQ-SRV 192.168.200.10`、`BR-A-PC 192.168.10.10` 与实验设备的 `Ethernet0/0`、`10.10.10.100`、`10.20.10.11` 不一致。
   - 这不是“同一实验网上的 10 个独立案例”，而是通用合成案例。可以用于测试 RAG/评分流程，但不能作为“企业实验网络故障测试”的现场证据。
3. **20 次评测结果没有保留在当前目录**
   - 当前 `data/ops.db`：`experiment_runs=0`、`experiment_results=0`、`test_cases=0`、`fault_injections=0`。
   - `test_cases/compare_results.json` 不存在（且被 `.gitignore` 排除）。
   - `audit_log` 中没有 `evaluation.completed` 事件。
   - `model_calls` 只有 2 次真实调用（P5 故障诊断 + 全量正常诊断），不是 20 次评测调用。
   - `docs/ACCEPTANCE_REPORT.md` 的 100%/100%、1.00/0.93、16,813.9/15,608.2 ms 只是文档汇总，无法从当前数据库或结果文件复核；报告第 7 节自述评测使用临时 SQLite/Chroma，临时库已不存在。
4. **评分方法偏关键词，不能等同于“诊断正确率”**
   - `test_cases/evaluation.py` 的 `score_answer()` 主要按 `must_mention`、`expected_devices`、`forbidden` 做字符串匹配；只要命中关键词和设备名，就可能判为 correct。
   - “可执行性”只检查是否有建议、是否标注人工确认、是否有复测方法；不检查命令是否单条、是否语法有效、目标设备是否正确。P5 的多段分号命令仍会被判为 1.0。
   - “正常网络”禁止词是精确短语 `默认路由缺失`；真实正常诊断写的是“默认路由（Gateway of last resort）配置缺失”，按当前匹配不一定被判错，说明评分对同义改写不稳健。
   - 因此，报告中的 100% 准确率不能直接视为人工专家认可的正确率。
5. **“有/无知识库”实际增益未体现**
   - 报告显示准确率 100% vs 100%，可执行性 1.00 vs 0.93，耗时 16.8s vs 15.6s；这只说明流程跑通，不能说明知识库对诊断正确率有提升。
   - P5 实际检索 Top 3 还把 route/DHCP 排在接口故障之前，进一步说明检索质量需要改进。

### 最低补做建议

- 在 EVE-NG 实验网上真实设置：
  - 链路中断 2 例（不同链路/不同设备）；
  - VLAN 配置错误 2 例（access VLAN 错配 / trunk allowed 缺 VLAN）；
  - 默认路由缺失 2 例（分支A、分支B 各一次）；
  - ACL 误拦截 2 例（不同 ACL 条目/方向）；
  - 正常网络 1 例、证据不足 1 例。
- 每个案例都执行真实采集，保存 `collection_runs`、原始输出、诊断记录和评测结果；在 `data/ops.db` 中保留 `experiment_runs`/`experiment_results`/`test_cases`，或提供可复现的 `compare_results.json` 和审计事件。
- 对评分增加人工复核或语义评分，至少校验 P5 这类多命令建议、错误复测目标、默认路由误报。

**结论：评测框架完成，真实故障注入和可核查的 20 次对比结果未完成。**

---

## 8. 结果展示与端到端演示（需求 8）

### 核对结果：基本完成

已实现：

- React 控制台：总览、设备与采集、诊断中心、知识库、设置；`/console/*` 由 FastAPI 托管。
- 设备状态：`/api/v1/devices` 返回最近采集状态、事实、观测时间和批次；页面展示可达/不可达/未采集。
- 诊断页面：拓扑 + AI 工作区；展示诊断结论、关键证据、可能原因、建议操作、知识依据、缺失信息；支持导出 Markdown/HTML。
- 旧版 `/diagnose` 页面：展示 6 个字段、知识库检索依据、会话消息、工具调用、模型调用审计和人工处理记录；`/evaluations` 页面可查看评测实验。
- 报告导出：`GET /api/v1/sessions/{id}/report?format=md|html` 和旧版 `/api/export/{diagnosis_id}`；`reports/diagnosis_1.md`、`reports/diagnosis_1.html` 存在。
- P5 真实演练记录：
  - 故障：BR-A-R2 `Ethernet0/0` shutdown；
  - 采集：`run_ab44ba...`（基线）、`run_5343d48...`（故障）、`run_ac14c9...`（复测）；
  - 诊断：`diagnosis_id=1`，`qwen-plus`，检索 4 篇；
  - 人工修复：`terminal_events` 记录备份完成后执行 `configure terminal` / `interface Ethernet0/0` / `no shutdown` / `end`；
  - 备份：`reports/backups/BR-A-R2_20261027_004218.cfg`，0600；
  - 复测：HQ-R1 → 10.20.10.11，100%，`manual_actions.recheck_result` 有记录；
  - 报告：`reports/diagnosis_1.md` / `.html`。
- 后端 151 个非 slow 用例通过（`pytest` 退出码 0）；`ruff check .` 通过；前端 16 个 Vitest 用例通过；`npm run build` 成功。

### 保留意见

1. **`test_cases/demo_e2e.py` 不是有效现场证据**
   - 该脚本用 `seed_records` 模拟故障，并在 `_simulate_recheck_connectivity()` 中直接打印：
     ```text
     Reply from 192.168.10.100: bytes=32 time=5ms TTL=60
     3 packets transmitted, 3 received, 0.0% packet loss
     ```
   - 它没有执行真实 ping，也没有真实设备；报告还把它写成“完整走通五环节”。如果验收时使用该脚本作为演示证据，会违反“不能仅使用预置文本模拟现场证据”。
   - 应明确标注为“UI/流程模拟”，或改用 P5 的真实采集/终端/复测记录。
2. **默认启动是 Mock 模式**
   - `python start.py` 默认 `--mock`，不连接设备、不自动采集；实际现场数据需要 `--live`。README 已说明，但验收时若直接用默认启动，页面展示的是示例数据，不是现场状态。
3. **React 诊断页没有直接展示“复测方法”和“人工处理记录”**
   - 旧版 `/diagnose` 页面有这两个区块；React 页面的 `_session_detail_dto` 未返回 `recheck_method`，也没有 `manual_actions`。需求 8 说“Web 界面或命令行展示……人工处理记录”，旧版页面可以满足，但新控制台不完整。
4. **P5 未走正式变更单**：同需求 6 的保留意见；如果验收要求“人工变更审批单”，需要补一次。
5. **报告与事实的局部不一致**：AI 复测目标错误、正常诊断默认路由误报，已在需求 5 说明；这些会进入导出的报告，影响“可核查运维记录”的质量。

**结论：展示能力基本完成，P5 真实演练可用；不要把 `demo_e2e.py` 当作现场证据。**

---

## 9. 本次实际执行的核对命令

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -q --disable-warnings` | 退出码 0；151 个非 slow 用例通过 |
| `.venv/bin/python -m ruff check .` | `All checks passed!` |
| `cd frontend && npm test -- --run` | 8 个测试文件、16 个用例通过 |
| `cd frontend && npm run build` | 构建成功 |
| `knowledge_base.retriever.retrieve()` 固定查询 + P5 问题 | 6 个领域查询 Top 3 类别命中；P5 问题 Top 3 为 ROUTE-001/DHCP-001/IF-004，未命中 IF-001 |
| SQLite 查询 `data/ops.db` | 9 台设备、4 个采集批次、1014 条观测、2 次真实模型调用、2 条诊断记录、2 条人工处理记录；`experiment_runs`/`experiment_results`/`test_cases`/`fault_injections` 均为 0 |
| 报告/备份文件检查 | `reports/diagnosis_1.md/html` 存在；`reports/backups/BR-A-R2_20261027_004218.cfg` 存在且权限 0600 |

---

## 10. 最终判定与建议

### 可以确认完成的部分

- 真实设备采集、SSH/SNMP/Ping、设备台账、VLAN/地址/路由/ACL 配置证据；
- 26 篇知识库、检索接口、RAG 调用、模型/模板/参数/来源记录；
- 只读白名单、写命令备份 + 一次性确认、终端审计、脱敏、报告导出；
- React 控制台和旧版页面、P5 真实链路故障演练；
- 后端/前端测试和构建。

### 不能确认完成的部分

1. **需求 7 的真实 4 类故障测试**：当前只有合成 `seed_records`，`fault_injections=0`，没有 VLAN/路由/ACL 的真实注入记录。
2. **需求 7 的 20 次评测可核查结果**：`experiment_runs`/`experiment_results` 为空，`compare_results.json` 不存在，`evaluation.completed` 审计事件不存在。
3. **需求 5 的“不编造设备状态”**：正常网络诊断把“证据未包含默认路由”写成“默认路由配置缺失”，与实际采集矛盾。
4. **需求 5 的建议可执行性**：P5 复测目标错误；修复建议是多段分号命令，不是单条命令；知识引用 `KB-IF-004` 与 administratively down 不符。
5. **需求 1 的可复现性**：实验网是外部 EVE-NG/ZeroTier，仓库没有拓扑/启动配置/重建脚本。

### 建议的验收前修复顺序

1. 先补需求 7：在真实实验网完成 4 类 × 2 案例 + 正常 + 证据不足，保存采集/诊断/评测/审计数据；至少提供一个可查询的 `experiment_id` 和结果文件。
2. 修复需求 5 的默认路由误报：让证据选择优先保留 `0.0.0.0/0`、直连、ACL 等关键事实；对模型输出增加“禁止断言证据中不存在的配置”后校验。
3. 修正 P5 复测目标为 `HQ-SRV 10.10.10.100` 或 `BR-A-PC 10.20.10.11`，并把多段修复命令拆成单条/明确人工步骤；增加命令格式校验。
4. 改善知识库检索：让 `KB-IF-001` 在“接口 administratively down/shutdown”类问题中优先命中；补充来源 URL/文档版本。
5. 把 `demo_e2e.py` 明确标为模拟脚本，验收演示改用 P5 的真实数据库记录；如果要求新控制台也展示人工处理记录/复测方法，补 DTO 和页面。
6. 保留实验网拓扑/启动配置，或提供可重复的 EVE-NG 导入文件和采集脚本，解决需求 1 的可复现性。

---

*本报告只新增本文件，未修改项目源码或既有文档。`data/ops.db`、`reports/`、`.env` 等为本地运行产物，已被 `.gitignore` 排除；核对时只读查询，未写入业务数据。*
