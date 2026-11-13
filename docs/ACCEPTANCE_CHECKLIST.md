# NetOps Console 验收清单（实操版）

> 适用对象：现场验收人、答辩人、复核人  
> 项目根目录：`/home/wyj/project6`  
> 编写日期：2026-11-12  
> 口径：**先看真实现场证据，再看可复现命令；不以 Mock 数据或历史成功状态替代当前状态。**

---

## 0. 验收前 10 分钟准备

### 0.1 进入项目并确认基线

```bash
cd /home/wyj/project6

python3 --version          # 要求 >= 3.12；项目 .venv 为 3.12
.venv/bin/python -V        # 实际执行验收请优先用项目解释器
node --version             # 要求 Node.js 18+
npm --version
git log --oneline -3
git status --short
```

判定：

- Python 3.12、Node 18+ 满足；
- 工作区无未提交的关键改动（`git status` 为空或只有明确说明的本地文件）；
- 当前提交包含验收所需的代码和文档。

### 0.2 启动服务

```bash
# 只检查环境，不安装、不构建、不启动
.venv/bin/python start.py --check

# 前台启动 Live 模式（真实 API + 真实设备采集）
.venv/bin/python start.py --live

# 或后台启动
nohup .venv/bin/python start.py --live > /tmp/netops-console.log 2>&1 &
echo $! > /tmp/netops-console.pid
tail -f /tmp/netops-console.log
```

判定：

```text
NetOps Console 已启动
运行模式: LIVE（真实 API）
访问地址: http://127.0.0.1:8000
```

> Mock 模式（`python3 start.py`）只用于演示 UI 和流程，**不能作为需求 7 的真实故障证据**。如果验收只看界面，可以用 Mock；如果验收真实采集/诊断/故障，必须 Live。

### 0.3 获取登录令牌并登录

```bash
TOKEN="$(grep '^APP_API_TOKEN=' .env | cut -d= -f2-)"

# 登录，保存 HttpOnly Cookie
curl -s -X POST http://127.0.0.1:8000/login \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\"}" \
  -c /tmp/netops.cookie

# 验证登录态（应返回 200 和 JSON）
curl -s -o /dev/null -w '%{http_code}\n' \
  -b /tmp/netops.cookie http://127.0.0.1:8000/api/v1/overview
```

判定：

- `/login` 返回 200；
- 未携带 Token 访问 `/api/v1/overview` 返回 401；
- 登录后返回 200；
- 浏览器访问 `http://127.0.0.1:8000/console/overview` 能看到实时数据。

> 不要执行 `set -x`，避免把 `APP_API_TOKEN` 打到终端日志里。

### 0.4 验收前刷新采集（重要）

`verify_acceptance.py` 的 `C7-latest-age` 要求最新批次不超过 0.5 小时。  
如果数据库里的最新批次是几天前的，先做一次全量采集，否则 C7 会显示 WARN/FAIL。

```bash
.venv/bin/python -m collector.run_collect \
  --trigger manual \
  --requested-by acceptance-2026-11-12
```

预期摘要：

```json
{
  "mode": "full",
  "device_count": 9,
  "ssh_commands_success": 39,
  "ssh_commands_failed": 0,
  "snmp_devices_success": 6,
  "connectivity_tests": 3,
  "connectivity_successes": 3
}
```

> 采集需要 EVE-NG/ZeroTier 和设备 SSH 可达。若网络未恢复，先做第 1 节，不要直接改设备。

---

## 1. 总览：8 项需求 → 验收环节

| 需求 | 验收环节 | 核心命令/操作 | 通过标准 | 主要证据 |
|---|---|---|---|---|
| 1. 实验网络 | 环境与接入 | `zerotier-cli`、`ping .218`、`nc`、SSH | 9 台设备、6/6 SSH、3/3 业务连通 | `docs/NETWORK_CONNECTION_GUIDE.md`、采集摘要 |
| 2. 状态采集 | 真实采集 | `collector.run_collect` | 39/39 SSH、9 设备、观测时间完整 | `collection_runs`、`state_observations` |
| 3. 知识库 | 索引与检索 | `knowledge_base.build_index`、API 状态 | ≥20 篇、5 类、元数据完整 | `articles.json`、`knowledge_*` 表 |
| 4. 大模型/RAG | 诊断 | `POST /api/v1/diagnose` | 真实模型调用、6 字段、证据可追溯 | `diagnosis_records`、`model_calls` |
| 5. 故障诊断 | 故障演练 | 终端备份→注入→采集→诊断→恢复→复测 | 有/无 KB 同证据、结论可复测 | `experiment_results`、`manual_actions` |
| 6. 操作控制 | 安全审计 | 终端写命令、审计查询 | 备份门、一次性令牌、0 条模型直写路径 | `terminal_*`、`audit_log` |
| 7. 效果测试 | 评测 | `run_real_case`、`compare_kb` | 10 用例/20 结果、4 类×2 | `experiment_runs/results` |
| 8. 结果展示 | Web/报告 | 页面、`/api/export` | 页面 200、MD/HTML 报告 | `reports/`、前端截图 |

---

## 2. 环节 A：环境与网络接入

### A1. 本机 ZeroTier 状态

```bash
sudo zerotier-cli info
sudo zerotier-cli peers
ip -br addr show zt6q3ma6ky
ip route | grep -E '10.10.146|192.168.249'
```

判定：

```text
200 info cdf0fdd993 1.16.2 ONLINE
89560f78b7  1.16.2  LEAF  DIRECT  ...   # EVE 成员
zt6q3ma6ky  10.10.146.195/24
10.10.146.0/24 dev zt6q3ma6ky
192.168.249.0/24 via 10.10.146.218 dev zt6q3ma6ky
```

### A2. EVE 管理入口与端口

```bash
ping -c 3 10.10.146.218

for p in 80 32769 32773 32775; do
  printf '%-6s ' "$p"
  timeout 3 nc -z -w 2 10.10.146.218 "$p" && echo OPEN || echo NO
done
```

判定：

- `ping` 有回复；
- `80` 开放（EVE Web，HTTP）；
- 控制台端口按拓扑开放（常见 `32769`–`32778`）。

> `10.10.146.218` 是 **EVE 管理入口**，不是网络设备。设备 SSH 地址是 `192.168.249.11`–`.16`，不要用 `.218:22` 当设备入口。

### A3. 设备 SSH 与业务连通性

```bash
for ip in 192.168.249.11 192.168.249.12 192.168.249.13 \
          192.168.249.14 192.168.249.15 192.168.249.16; do
  printf '%-16s ' "$ip:22"
  timeout 3 nc -z -w 2 "$ip" 22 && echo OPEN || echo NO
done
```

业务路径（从 HQ-R1 源 ping）：

```bash
# 可在 Web 诊断中心/终端执行，也可用采集摘要里的 connectivity=3/3 作为证据
# HQ-R1 -> 10.10.10.100 / 10.20.10.11 / 10.30.10.11
```

### A4. 背后的原理

- **ZeroTier** 提供二层 Overlay，本机 `10.10.146.195` 与 EVE `10.10.146.218` 在同一虚拟网络；
- **EVE-NG** 提供实验拓扑和旁路管理入口；
- **管理网 `192.168.249.0/24`** 通过 `.218` 路由到 6 台网络设备；
- **控制面与数据面要分开看**：`peers` 显示 `DIRECT` 只说明 ZeroTier 控制面建立；只有 `ping`/ARP/`zt RX` 有数据，才说明 L2 数据面通；
- **SSH/SNMP/Ping** 是后续采集和连通性测试的三种通道。

### A5. 失败判断

| 现象 | 判断 | 下一步 |
|---|---|---|
| `peers` 没有 `89560f78b7` | EVE 未上线/未加入网络 | 查 EVE 控制台 `listnetworks` |
| peer `DIRECT`，但 ARP `INCOMPLETE`、`zt RX=0` | 控制面通、L2 数据面未通 | 查 EVE 侧 `zt` 接口/防火墙/Flow Rules |
| `.218` 可达，但设备 `:22` 不通 | 管理网路由或设备 SSH 服务问题 | 查 `192.168.249.0/24` 路由，不要改设备地址 |
| 设备 SSH 成功，但业务 ping 不通 | 进入第 6 节故障诊断 | VLAN/路由/ACL/链路 |

### A6. 技术组成

ZeroTier One 1.16.2、EVE-NG、Cisco IOSv/IOSvL2、VPCS、ICMP、SSH（Netmiko）、SNMP（pysnmp）。

---

## 3. 环节 B：启动、登录与鉴权

### B1. 启动检查

```bash
.venv/bin/python start.py --check
```

检查项：

1. Python ≥ 3.12；
2. `.venv` 存在或可创建；
3. 后端依赖；
4. `.env` 与 `APP_API_TOKEN`（长度 ≥ 16）；
5. Node/npm 与前端 `dist`；
6. 知识库索引；
7. `127.0.0.1:8000` 端口可用。

### B2. 登录与鉴权

```bash
TOKEN="$(grep '^APP_API_TOKEN=' .env | cut -d= -f2-)"

# 1) 未登录访问 API -> 401
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8000/api/v1/overview

# 2) 登录 -> 200 + Set-Cookie
curl -s -X POST http://127.0.0.1:8000/login \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\"}" \
  -c /tmp/netops.cookie

# 3) 登录后访问 -> 200
curl -s -o /dev/null -w '%{http_code}\n' \
  -b /tmp/netops.cookie http://127.0.0.1:8000/api/v1/overview
```

判定：

```text
未登录：401
登录：200
登录后：200
```

### B3. 背后原理

- `start.py` 是唯一启动入口，按“Python → venv → 依赖 → 配置 → 前端 → 知识库 → 端口”七步检查；
- FastAPI 的 lifespan 启动 `bootstrap_runtime()`，初始化数据库、知识库状态和终端服务；
- `TokenAuthMiddleware` 支持三种 Token 携带方式：`Authorization: Bearer`、`X-API-Token`、HttpOnly Cookie；
- 页面请求未登录时 302 到 `/login`，API 请求返回 401；
- `APP_ENABLE_DOCS=false` 时 `/docs`、`/redoc`、OpenAPI 默认关闭。

### B4. 技术组成

`start.py`、uvicorn、FastAPI、`webapp/security.py`、React/Vite、SQLite、Chroma、python-dotenv。

---

## 4. 环节 C：真实采集与证据链

### C1. 执行一次真实采集

```bash
# 全量 9 台设备
.venv/bin/python -m collector.run_collect \
  --trigger manual \
  --requested-by acceptance

# 只采指定设备（快速复核）
.venv/bin/python -m collector.run_collect \
  --devices HQ-R1,BR-A-R2,BR-A-SW2,BR-A-PC \
  --trigger manual \
  --requested-by acceptance-subset
```

### C2. 查询采集结果

```bash
sqlite3 data/ops.db <<'SQL'
.headers on
.mode column

SELECT run_id, trigger_type, started_at_utc, published_at_utc
FROM collection_runs
ORDER BY started_at_utc DESC
LIMIT 3;

SELECT COUNT(*) AS observations FROM state_observations;
SELECT COUNT(*) AS missing_time
FROM state_observations WHERE observed_at_utc IS NULL;
SELECT COUNT(*) AS unpublished
FROM collection_runs WHERE published_at_utc IS NULL;
SQL
```

预期：

```text
observations >= 100（当前库 6467）
missing_time = 0
unpublished = 0
最新批次 SSH 成功率 >= 95%（当前 100%）
```

### C3. 看一台设备的证据链

```bash
sqlite3 data/ops.db <<'SQL'
.headers on
.mode column

SELECT d.device_name, ci.capture_type, ci.collection_status,
       ci.observation_status, ci.command_or_tool
FROM collection_items ci
JOIN device_capture_results r ON r.result_id = ci.result_id
JOIN devices d ON d.device_id = ci.device_id
WHERE r.run_id = (SELECT run_id FROM collection_runs
                  ORDER BY started_at_utc DESC LIMIT 1)
ORDER BY d.device_name, ci.capture_type
LIMIT 40;
SQL
```

判定：

- 每台设备有接口/路由/配置/邻居或 ACL 等多类事实；
- 成功项有 `raw_blob_id`，失败项有 `error_message`；
- 不可达设备标记 `unreachable/failed`，不用历史成功状态补齐。

### C4. 背后原理

- **“最新已发布快照”是当前状态的唯一依据**：历史成功采集不能替代当前状态；
- 每条采集项进入 `collection_items`，原始输出进 `raw_blobs`，解析后的事实进 `state_observations`；
- 采集批次 `collection_runs` 发布后，诊断只读已发布结果；
- 超过 30 分钟的证据会标记为 stale，不能用于“当前状态”结论。

### C5. 技术组成

Netmiko（单会话多命令）、pysnmp、ICMP Ping、`collector/state_parser.py`、SQLite 表 `collection_runs / device_capture_results / collection_items / raw_blobs / state_observations`。

---

## 5. 环节 D：知识库构建与检索

### D1. 查看知识库状态

```bash
curl -s -b /tmp/netops.cookie \
  http://127.0.0.1:8000/api/v1/knowledge-base/status
```

判定：

- 文章数 ≥ 20（当前 26）；
- 类别覆盖 VLAN/路由/DHCP/ACL/接口故障；
- 索引版本为 active；
- `source_url` 覆盖为 0/26 时是 WARN，不是启动阻塞项。

### D2. 重建索引（可选）

```bash
.venv/bin/python -m knowledge_base.build_index --rebuild
```

> 重建会重新生成 Chroma 集合，首次可能需要下载/加载 fastembed 模型。验收时间紧时可跳过，直接使用现有索引。

### D3. 检索验证

```bash
curl -s -b /tmp/netops.cookie \
  'http://127.0.0.1:8000/api/v1/knowledge-base/articles?q=VLAN' | head
```

或直接跑一次诊断，检查 `diagnosis_records.retrieved_doc_ids_json` 是否包含相关类别。

### D4. 背后原理

- `knowledge_base/articles.json` 是知识源；
- 同步到关系表 `knowledge_articles`，保留来源、适用设备、版本；
- fastembed 生成向量（`BAAI/bge-small-zh-v1.5`），写入 Chroma；
- 检索采用混合策略：向量距离 + BM25 + 类别加成，按权重融合后取 Top-K；
- 每次检索写 `retrieval_queries` / `retrieval_hits`，便于复核排名和来源。

### D5. 技术组成

`knowledge_base/build_index.py`、`knowledge_base/retriever.py`、fastembed、Chroma、SQLite 知识表。

---

## 6. 环节 E：RAG 诊断

### E1. 通过 API 发起诊断

```bash
curl -s -b /tmp/netops.cookie \
  -X POST http://127.0.0.1:8000/api/v1/diagnose \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "分支A的终端访问总部服务器一直超时，路由是不是有问题？",
    "use_kb": true,
    "requested_by": "acceptance"
  }' | head -c 2000
```

`use_kb=false` 用同一问题再跑一次，作为无知识库对照。

### E2. 检查诊断记录

```bash
sqlite3 data/ops.db <<'SQL'
.headers on
.mode column

SELECT id, use_kb, model_id, prompt_template_id,
       response_time_ms, total_response_time_ms, created_at
FROM diagnosis_records
ORDER BY id DESC LIMIT 4;

SELECT call_id, status, error_code, error_message
FROM model_calls ORDER BY created_at_utc DESC LIMIT 4;
SQL
```

再查证据、检索、上下文：

```bash
sqlite3 data/ops.db <<'SQL'
SELECT id, json_array_length(evidence_json) AS evidence_items,
       json_array_length(retrieved_doc_ids_json) AS retrieved_docs
FROM diagnosis_records ORDER BY id DESC LIMIT 4;
SQL
```

### E3. 背后原理

诊断链路：

```text
问题
→ extract_relevant_devices（设备识别）
→ build_evidence_for_devices（取最新已发布证据）
→ retrieve（知识库检索，可关闭）
→ prompt_templates（system + 问题 + 证据 + 知识）
→ LLM（qwen-plus）
→ answer_validation（字段/证据 ID/命令/复测校验）
→ diagnosis_records + model_calls + retrieval_queries + context_snapshot
```

关键点：

- 有/无知识库必须使用**同一采集批次、同一 evidence_json**，否则结果不可比；
- 回答必须包含 6 个字段：现象、证据、原因、检查、建议、复测；
- `evidence_used.observation_id` 必须存在于本次证据；
- 涉及 `shutdown / ip route / access-list / switchport` 的建议必须 `requires_manual_confirmation=true`；
- 模型失败/输出截断时记录 `model_status=error`，不静默编造。

### E4. 技术组成

`rag_engine/device_extractor.py`、`rag_engine/evidence_builder.py`、`knowledge_base/retriever.py`、`rag_engine/prompt_templates.py`、`rag_engine/llm_client.py`、`rag_engine/answer_validation.py`、qwen-plus、SQLite/Chroma。

---

## 7. 环节 F：故障注入 → 诊断 → 修复 → 复测（核心）

### F1. 验收方式选择

| 方式 | 时间 | 做什么 | 适用 |
|---|---|---|---|
| 快速验收 | 30–45 分钟 | 启动、全量采集、审计、看现有实验/报告 | 时间紧，验证系统和已有证据 |
| 单故障演练 | 20–30 分钟 | 选 1 个故障（推荐 VLAN 或链路），完整跑一遍 | 验证“注入→诊断→恢复→复测”闭环 |
| 全量复现 | 1–2 天 | 4 类×2 + 正常 + 证据不足，20 条结果 | 严格按需求 7 逐条复现 |

> 已有真实 campaign 数据（14 实验/28 结果/10 次注入）可以直接审计；不要求每次验收都重新注入全部故障。

### F2. 通用安全流程（任何写操作都必须这样）

```text
打开审计终端
→ 确认已备份（backupStatus=completed）
→ 输入写命令
→ 系统生成一次性令牌（120s，绑 session + 命令哈希）
→ 人工确认后下发
→ 命令和输出落库
→ 修复后复测
→ 关闭会话
```

Web UI 路径：

```text
诊断中心 → 选择设备/诊断会话 → 打开终端
→ 勾选“已确认运行配置已备份”
→ 建立受审计终端
→ 先“备份”
→ 输入命令
→ 写命令确认
```

### F3. 四类故障的命令（示例）

> 以下命令用于验收演练。执行前必须确认管理网不受影响，执行后必须恢复原配置。

#### F3.1 链路 down（BR-A-R2 Ethernet0/0）

```cisco
configure terminal
interface Ethernet0/0
 shutdown
end
```

恢复：

```cisco
configure terminal
interface Ethernet0/0
 no shutdown
end
```

预期：业务连通性 0/1；恢复后 1/1。管理网走 `Ethernet3/0`，不受影响。

#### F3.2 VLAN 错配（BR-A-SW2 Ethernet1/0）

```cisco
configure terminal
interface Ethernet1/0
 switchport access vlan 20
end
```

恢复：

```cisco
configure terminal
interface Ethernet1/0
 switchport access vlan 10
end
```

预期：`BR-A-PC` 不可达；恢复后 VLAN 10、连通性 1/1。  
证据必须包含 `VLAN 20: ports=Ethernet1/0`，不能只看“VLAN 20”。

#### F3.3 默认路由缺失（BR-A-R2 或 BR-B-R3）

```cisco
configure terminal
no ip route 0.0.0.0 0.0.0.0 172.16.1.1
end
```

恢复：

```cisco
configure terminal
ip route 0.0.0.0 0.0.0.0 172.16.1.1
end
```

分支B 对应 `172.16.2.1`。  
预期：分支路由器访问 `10.10.10.100` 失败；恢复后成功。管理网仍可达。

#### F3.4 ACL 过度封锁（HQ-R1 分支接口入方向）

```cisco
configure terminal
ip access-list extended PILOT-BLOCK-A
 deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255
 permit ip any any
end
configure terminal
interface Ethernet1/0
 no ip access-group WAN-IN in
 ip access-group PILOT-BLOCK-A in
end
```

恢复：

```cisco
configure terminal
interface Ethernet1/0
 no ip access-group PILOT-BLOCK-A in
 ip access-group WAN-IN in
end
configure terminal
no ip access-list extended PILOT-BLOCK-A
end
```

分支B 对应 `10.30.10.0/24` 和 `Ethernet2/0`，ACL 名 `PILOT-BLOCK-B`。  
预期：指定源网段到 `10.10.10.0/24` 失败；恢复后 `WAN-IN` 重新生效。

### F4. 采集与诊断

```bash
# 故障状态采集（按故障影响范围选择设备）
.venv/bin/python -m collector.run_collect \
  --devices BR-A-R2,BR-A-SW2,BR-A-PC \
  --trigger manual \
  --requested-by acceptance-fault
```

然后在 Python 中调用真实评测（或使用 Web 诊断）：

```python
from test_cases.real_evaluation import run_real_case

payload = run_real_case(
    case_external_id="REAL_ACCEPT_01",
    category="vlan_misconfig",
    question="分支A的终端无法访问总部服务器了，怀疑是VLAN配置问题。",
    expected={
        "must_mention": ["VLAN", "20"],
        "must_mention_any": ["Ethernet1/0", "Et1/0", "Ethernet 1/0"],
        "expected_devices": ["BR-A-SW2"],
        "forbidden": ["默认路由缺失是根因", "接口物理损坏是唯一原因"],
        "require_recheck": True,
        "expect_no_change": False,
    },
    expected_notes="BR-A-SW2 Ethernet1/0 access VLAN 10 -> 20。",
    suite_name="EVE-NG 真实故障评测",
    created_by="acceptance",
    device_name="BR-A-SW2",
    fault_type="vlan_misconfig",
    fault_description="Ethernet1/0 access VLAN 10 -> 20",
)
print(payload["experiment_id"])
print(payload["results"])
```

### F5. 恢复与复测

```bash
# 恢复配置后重新采集
.venv/bin/python -m collector.run_collect \
  --devices BR-A-R2,BR-A-SW2,BR-A-PC \
  --trigger manual \
  --requested-by acceptance-recheck
```

复测要求：

- 设备配置回到基线（VLAN 10 / 默认路由存在 / 接口 up / `WAN-IN` 恢复）；
- 业务 ping 成功；
- `manual_actions` 有一条 `completed` 记录，包含 `backup_ref` 和 `recheck_result`；
- 报告 `reports/diagnosis_<id>.md/html` 生成。

### F6. 背后原理

- **管理面与业务面分离**：管理网走独立接口/网段，业务故障不应切断 SSH；
- **备份门**：写命令前必须完成实际配置备份；
- **一次性令牌**：绑定会话与命令哈希，120 秒有效，防重放；
- **先落库再发送**：命令意图、确认、输出都进审计；
- **复测是结论的一部分**：没有复测的“修复”不算完成；
- **故障可逆**：所有演练脚本都要有 `finally` 强制恢复。

### F7. 技术组成

`control/terminal_service.py`、`control/write_policy.py`、`control/terminal_confirmations.py`、`terminal_sessions/terminal_events`、`manual_actions`、`reports/backups/*.cfg`、Netmiko。

---

## 8. 环节 G：评测与指标

### G1. 只读验收审计（推荐先跑）

```bash
.venv/bin/python scripts/verify_acceptance.py \
  --db /home/wyj/project6/data/ops.db \
  --json-output /tmp/acceptance.json
```

当前参考结果（2026-11-12）：

```text
PASS：N1–N4、C1–C6、K1–K3、L1–L5
WARN：C7 最新批次 70.5 小时（先做一次全量采集即可 PASS）
WARN：K4 source_url 0/26
PASS：E1 实验 14、E2 结果 28、E3 用例 11、E4 真实故障注入 10
WARN：O1 变更单 0
PASS：O2–O3、P1–P3
```

### G2. 查看有效 campaign 指标

```bash
sqlite3 data/ops.db <<'SQL'
.headers on
.mode column

SELECT experiment_id,
       json_extract(summary_json, '$.case_external_id') AS case_id,
       json_extract(summary_json, '$.by_variant.with_kb.accuracy') AS acc_with,
       json_extract(summary_json, '$.by_variant.without_kb.accuracy') AS acc_without,
       started_at_utc
FROM experiment_runs
ORDER BY started_at_utc;
SQL
```

有效 campaign 参考值：

```text
有知识库：8C/1P/1W，准确率 85.0%，可执行性 0.875
无知识库：7C/2P/1W，准确率 80.0%，可执行性 1.000
```

> `REAL_NORMAL_01` 是历史 stale 负向控制；`REAL_VLAN_01` 是真实错误结果（暴露证据缺陷后仍保留）；早期 ACL/证据不足失败尝试在 `summary_json.validity` 标记为 superseded/invalid。有效指标按“每个用例取最终实验”计算。

### G3. 合成评测（回归用，不作为现场证据）

```bash
.venv/bin/python -m test_cases.compare_kb \
  --skip-kb-build \
  --output /tmp/compare_results.json
```

> `compare_kb` 使用 `seed_records` 合成场景，用于验证评分/RAG 回归；**不能作为需求 7 的真实故障证据**。真实证据用 `test_cases/real_evaluation.py` + EVE 设备。

### G4. 背后原理

- `score_answer` 按关键词覆盖、设备覆盖、禁止性断言、可执行建议、复测方法打分；
- `evidence_hash` 对同一证据做哈希，有/无 KB 必须一致；
- `aggregate_results` 汇总 correct/partial/wrong/error/skipped，准确率 = correct + 0.5×partial；
- 结果写入 `experiment_results`，可按实验、用例、KB 开关查询；
- 评分只依赖回答和预先声明的期望，不把标准答案写回知识库。

### G5. 技术组成

`test_cases/evaluation.py`、`test_cases/real_evaluation.py`、`test_cases/compare_kb.py`、`experiment_runs/experiment_results`、`evidence_hash`。

---

## 9. 环节 H：安全、审计与变更单

### H1. 安全回归测试

```bash
.venv/bin/python -m pytest -q \
  tests/test_whitelist.py \
  tests/test_write_policy.py \
  tests/test_write_confirmations.py \
  tests/test_terminal_execution_security.py \
  tests/test_sanitize.py \
  tests/test_security.py
```

判定：全部通过。

### H2. 检查“模型不能直接改设备”

```bash
# 诊断/RAG 代码中不应存在直接下发配置的路径
grep -rn "send_config_set\|terminal_manager.execute" rag_engine/ || echo "OK: 无直接下发路径"
```

判定：`rag_engine/` 中无直接写设备代码；写命令只经 `control/terminal_service.py`。

### H3. 审计查询

```bash
sqlite3 data/ops.db <<'SQL'
.headers on
.mode column

SELECT session_id, device_name, operator, status, backup_status,
       started_at_utc, ended_at_utc
FROM terminal_sessions
ORDER BY started_at_utc DESC LIMIT 5;

SELECT event_id, session_id, event_type, source, command_text, exit_code
FROM terminal_events
ORDER BY created_at_utc DESC LIMIT 10;

SELECT id, diagnosis_id, operator, status, backup_ref, recheck_result
FROM manual_actions
ORDER BY id DESC LIMIT 5;

SELECT audit_id, actor, action, target_type, target_id, created_at_utc
FROM audit_log
ORDER BY created_at_utc DESC LIMIT 10;

SELECT change_id, status, title, requested_by, created_at_utc
FROM change_requests
ORDER BY created_at_utc DESC LIMIT 5;
SQL
```

判定：

- 每条写命令有终端事件、命令文本、输出/错误；
- 人工处理记录有备份路径和复测结果；
- `audit_log` 有诊断、导出、评测、变更等事件；
- `change_requests` 可以为 0（P5 可豁免），但正式变更流程应能通过 API 创建。

### H4. 变更单流程（可选）

```bash
# 创建变更单（必须关联 diagnosis_id 或 conclusion_id）
curl -s -b /tmp/netops.cookie \
  -X POST http://127.0.0.1:8000/api/changes \
  -H 'Content-Type: application/json' \
  -d '{
    "diagnosis_id": 17,
    "device_name": "HQ-R1",
    "change_type": "acl",
    "title": "恢复 WAN-IN ACL",
    "description": "验收演练后恢复",
    "risk_level": "medium",
    "operator": "acceptance",
    "rollback_plan": "重新应用 PILOT-BLOCK-A",
    "planned_at_utc": "2026-11-12T10:00:00+08:00"
  }'
```

后续接口：

```text
POST /api/changes/{id}/approve
POST /api/changes/{id}/start
POST /api/changes/{id}/backup
POST /api/changes/{id}/steps/{step_id}/execute
POST /api/changes/{id}/recheck
POST /api/changes/{id}/complete
```

### H5. 背后原理

- 写策略 **deny-by-default**：只有明确只读白名单（show/display/ping/traceroute 等）不需要确认；
- 写命令必须完成配置备份 + 一次性令牌；
- 令牌 120 秒有效，绑定 `session_id + 命令哈希`，防重放；
- 所有命令、输出、错误都进 `terminal_events`，审计链完整；
- 模型只生成建议，人工确认后才可能进入终端；
- 敏感信息（密码、Token、SNMP community）脱敏后才进 Prompt。

### H6. 技术组成

`control/write_policy.py`、`control/terminal_service.py`、`control/terminal_confirmations.py`、`rag_engine/sanitize.py`、`collector/db/changes.py`、`audit_log`。

---

## 10. 环节 I：Web 展示与报告导出

### I1. 页面验收

```bash
for path in overview assets diagnosis knowledge settings; do
  printf '%-12s ' "$path"
  curl -s -o /dev/null -w '%{http_code}\n' \
    -b /tmp/netops.cookie "http://127.0.0.1:8000/console/$path"
done
```

判定：全部 200。

浏览器检查：

```text
http://127.0.0.1:8000/console/overview     总览：设备 9/9、最新采集、拓扑
http://127.0.0.1:8000/console/assets       设备与采集：台账、采集批次
http://127.0.0.1:8000/console/diagnosis    诊断中心：拓扑 + AI 工作区 + 终端 Dock
http://127.0.0.1:8000/console/knowledge    知识库：26 篇、索引状态、检索
http://127.0.0.1:8000/console/settings     设置：模型/环境/采集
```

### I2. 报告导出

```bash
# Markdown
curl -s -b /tmp/netops.cookie \
  http://127.0.0.1:8000/api/export/17 \
  -o /tmp/diagnosis_17.md

# HTML（新版 API）
curl -s -b /tmp/netops.cookie \
  'http://127.0.0.1:8000/api/v1/sessions/<session_id>/report?format=html' \
  -o /tmp/diagnosis_17.html
```

或使用 UI 诊断中心的“导出报告 / 导出 HTML”。

判定：

- Markdown 200，包含：模型/参数、证据、知识检索、现象、原因、检查、建议、复测、人工记录；
- HTML 200，浏览器可渲染；
- PDF 等不支持格式返回 400（明确失败，不静默）。

### I3. 背后原理

- React SPA 由 FastAPI 托管，`/console/*` 返回 `frontend/dist/index.html`；
- 旧版页面/API 保留兼容；
- `report_export.py` 从 `diagnosis_records`、证据、检索、人工记录生成 Markdown/HTML；
- 报告中的模型 ID、Prompt 模板、参数、检索来源、人工记录都可回溯到数据库。

### I4. 配图交付（报告/论文用）

```bash
ls pic/
cat pic/README.md
head -5 pic/manifest.csv
```

判定：

- `pic/` 中有 14 张待绘制图（9 张示意图 + 3 张真实数据图 + 2 张实机截图组图）；
- `pic/manifest.csv` 覆盖全部 32 条清单，`已绘制` 的 18 条有记录但没有重新生成；
- 三张数据图来自真实 `data/ops.db`；
- 图10-2/10-3 来自实机 Web 和真实报告。

### I5. 技术组成

FastAPI、Jinja2、React/Vite、`webapp/report_export.py`、`webapp/api/v1/router.py`、`webapp/routers/legacy_api.py`、`scripts/generate_pic_figures.py`。

---

## 11. 一键验收命令（建议顺序）

```bash
cd /home/wyj/project6

# 1) 后端测试 + 静态检查
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .

# 2) 前端测试 + 构建
cd frontend
npm test
npm run build
cd ..

# 3) 启动检查（不安装/不构建）
.venv/bin/python start.py --check

# 4) 启动 Live（另开终端）
.venv/bin/python start.py --live

# 5) 登录并刷新采集
TOKEN="$(grep '^APP_API_TOKEN=' .env | cut -d= -f2-)"
curl -s -X POST http://127.0.0.1:8000/login \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\"}" -c /tmp/netops.cookie
.venv/bin/python -m collector.run_collect \
  --trigger manual --requested-by acceptance

# 6) 只读审计
.venv/bin/python scripts/verify_acceptance.py \
  --db /home/wyj/project6/data/ops.db \
  --json-output /tmp/acceptance.json
```

验收判定：

```text
pytest：0 failed
ruff：All checks passed
npm test：全部通过
npm run build：成功
start.py --check：通过
采集：9 设备 / 39 SSH / 0 失败 / 连通性 3/3
audit：C7 通过（0.0 小时）；K4/O1 如无特殊要求可标 WARN
```

---

## 12. 验收记录模板

| 序号 | 环节 | 命令/操作 | 预期输出 | 实际输出 | 判定 | 证据路径 | 验收人/日期 |
|---|---|---|---|---|---|---|---|
| 1 | 环境 | `python3 --version` | 3.12 |  |  |  |  |
| 2 | ZeroTier | `zerotier-cli peers` | 89560f78b7 DIRECT |  |  |  |  |
| 3 | EVE | `ping 10.10.146.218` | 可达 |  |  |  |  |
| 4 | 设备 SSH | `nc -z 192.168.249.11 22` | OPEN |  |  |  |  |
| 5 | 启动 | `start.py --check` | 通过 |  |  |  |  |
| 6 | 登录 | `POST /login` | 200 + Cookie |  |  |  |  |
| 7 | 采集 | `collector.run_collect` | 9 设备/39 SSH |  |  |  |  |
| 8 | 知识库 | `/api/v1/knowledge-base/status` | 26 篇/5 类 |  |  |  |  |
| 9 | 诊断 | `POST /api/v1/diagnose` | 6 字段/证据 ID |  |  |  |  |
| 10 | 故障演练 | 备份→注入→采集→诊断→恢复→复测 | 0/1 → 1/1 |  |  |  |  |
| 11 | 审计 | `audit_log`/`manual_actions` | 记录完整 |  |  |  |  |
| 12 | 评测 | `verify_acceptance.py` | E2/E3/E4 PASS |  |  |  |  |
| 13 | 报告 | `/api/export/{id}` | MD/HTML 200 |  |  |  |  |

---

## 13. 判定口径与常见问题

### 13.1 先刷新再审计

`C7-latest-age` 的要求是 0.5 小时内。验收当天先跑一次全量采集，否则会看到“70.5 小时”的 WARN。刷新后 C7 应变为 PASS。

### 13.2 K4 `source_url 0/26` 是 WARN

知识库 26 篇都有来源名称、适用设备、版本，但 `source_url` 为空。  
如果验收标准要求“可核验 URL”，这是明确缺口；如果不要求 URL，标 WARN 并在报告中说明即可。

### 13.3 O1 变更单 0 是 WARN

真实演练通过“审计终端 + 备份 + 人工处理记录”留痕，但没有创建正式 `change_requests`。  
如需关闭 O1，用 `/api/changes` 走一遍创建→审批→执行→复测→完成。

### 13.4 Mock 与 Live 不能混用

- Mock：示例数据，验证 UI/流程；
- Live：真实 API/设备，验证采集/诊断/故障；
- 需求 7 的现场证据必须来自 Live + EVE 真实设备，不能用 `seed_records`。

### 13.5 “评分 correct”不等于“实际正确”

评测是按预先声明的关键词/设备/禁止项打分。  
验收时要同时看：

- `evaluation_json.matched_terms / missing_terms`；
- 诊断回答是否真的指向故障端口/设备；
- 是否有 `rescore_history`（历史更正保留）。

例如 VLAN 试点 1 最初显示 correct，但回答没有点名 `Ethernet1/0`，后来按加强标准更正为 wrong；这类历史记录要按“最终有效实验”统计。

### 13.6 SNMP 偶发超时

部分批次 SNMP 可能 3/6，SSH/连通性仍正常。  
判定时以“最新批次 + 重试一次”为准；不要把一次 SNMP 超时当成整体采集失败。

### 13.7 不要把 EVE 管理入口当设备

`10.10.146.218` 是 EVE 管理入口；设备 SSH 是 `192.168.249.11`–`.16`。  
`10.10.146.218:22` 不是设备 SSH，不能用于 Netmiko 采集。

### 13.8 证据时效

超过 30 分钟的证据不能作为“当前状态”结论。  
验收时看 `collected_at`/`observed_at_utc` 和 `is_stale`；过期就重新采集。

---

## 14. 技术组成总览

| 层 | 组件 | 关键文件/位置 |
|---|---|---|
| 实验网络 | EVE-NG、ZeroTier、Cisco IOSv/IOSvL2、VPCS | EVE 管理入口 `.218`、`192.168.249.0/24` |
| 采集 | Netmiko SSH、pysnmp、ICMP Ping、状态解析 | `collector/run_collect.py`、`collector/state_parser.py` |
| 存储 | SQLite（采集/诊断/评测/审计）、Chroma | `data/ops.db`、`data/chroma` |
| 知识库 | articles.json、fastembed、BM25+向量混合检索 | `knowledge_base/` |
| RAG | 设备识别、证据装配、Prompt、LLM、回答校验 | `rag_engine/` |
| 模型 | qwen-plus（OpenAI 兼容接口） | `.env` `LLM_MODEL`/`LLM_BASE_URL` |
| 控制 | 只读白名单、写策略、备份门、一次性令牌、审计终端 | `control/` |
| 评测 | 评分、证据哈希、真实/合成评测 | `test_cases/` |
| 展示 | FastAPI、React/Vite、Jinja2、报告导出 | `webapp/`、`frontend/` |
| 启动 | 单入口七步检查、uvicorn | `start.py` |

---

*本清单按 2026-11-12 的代码和数据库状态编写；命令均可从项目根目录执行。*
