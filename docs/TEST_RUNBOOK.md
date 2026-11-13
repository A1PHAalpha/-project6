# NetOps Console 测试运行手册（Windows + 本机 EVE-NG）

> 基线日期：2026-11-13
> 适用环境：Windows 10/11、项目 `.venv`、VMware 本机运行 EVE-NG、ZeroTier 仅用于访问 EVE 管理地址 `10.10.146.218`
> 设备接入：`admin` + `.env` 的 `DEVICE_PASSWORD`，SSH 登录即 privilege 15，**不需要 enable 密码**

本手册按“基线快照 → 启动 → 执行 → 判定 → 恢复 → 复核”组织。每一类测试都给出独立的恢复动作；
真实故障注入部分给出逐条逆操作命令。所有命令在项目根目录 `C:\Users\popcorn\Desktop\-project6` 执行。

---

## 0. 三条铁律

1. **先备份，再注入；先恢复，再分析。** 设备配置、`data/ops.db`、`data/chroma/`、`.env` 四样缺一不可。
2. **测试数据与演示数据分离。** 可重复测试用临时 `SQLITE_DB_PATH`；正式演示才写 `data/ops.db`。
3. **写设备只走人工终端或变更单。** 模型、自动采集、诊断链路都不得直接下发配置。

---

## 1. 环境基线

### 1.1 设备与控制台对照

| 项目设备名 | 拓扑节点 | 管理 IP | SSH | EVE 控制台 | 管理口 |
|---|---|---|---|---|---|
| HQ-R1 | R-HQ | 192.168.249.11 | 22 | 10.10.146.218:32769 | Ethernet3/0 |
| BR-A-R2 | R-B1 | 192.168.249.12 | 22 | :32770 | Ethernet3/0 |
| BR-B-R3 | R-B2 | 192.168.249.13 | 22 | :32771 | Ethernet3/0 |
| HQ-SW1 | SW-HQ | 192.168.249.14 | 22 | :32772 | Vlan999 |
| BR-A-SW2 | SW-B1 | 192.168.249.15 | 22 | :32773 | Vlan999 |
| BR-B-SW3 | SW-B2 | 192.168.249.16 | 22 | :32774 | Vlan999 |
| HQ-SRV | SRV-01 | 10.10.10.100 | 无 | :32775 | — |
| BR-A-PC | PC-B1-01 | 10.20.10.11 | 无 | :32777 | — |
| BR-B-PC | PC-B2-01 | 10.30.10.11 | 无 | :32778 | — |

VPCS 终端没有 SSH 服务，终端连通性通过 `telnet 10.10.146.218 3277x` 的控制台执行 ping，
或由 HQ-R1 作为 ping 源执行。

### 1.2 当前真实拓扑基线（已实读）

```text
HQ-R1     Et3/0 192.168.249.11
          Et0/0.10 10.10.10.1   Et0/0.20 10.10.20.1   Et0/0.99 10.10.99.1
          Et1/0 172.16.1.1  -> BR-A-R2 Et1/0 172.16.1.2
          Et2/0 172.16.2.1  -> BR-B-R3 Et1/0 172.16.2.2
          静态: 10.20.0.0/16 via 172.16.1.2；10.30.0.0/16 via 172.16.2.2
          无默认路由

BR-A-R2   Et0/0.10 10.20.10.1  Et0/0.20 10.20.20.1  Et0/0.99 10.20.99.1
          默认路由 0.0.0.0/0 via 172.16.1.1

BR-B-R3   Et0/0.10 10.30.10.1  Et0/0.20 10.30.20.1  Et0/0.99 10.30.99.1
          默认路由 0.0.0.0/0 via 172.16.2.1

交换机     Et0/0 trunk 1-4094（上联路由器 Et0/0）
          Et1/0 access VLAN10；Et2/0 access VLAN20；Et3/0 access VLAN999
          Vlan99 10.x.99.11；Vlan999 192.168.249.1x（管理）

HQ-R1 ACL WAN-IN（inbound 应用在 Et1/0、Et2/0）
          10 permit 10.20.10.0/24 -> 10.10.10.0/24
          20 permit 10.30.10.0/24 -> 10.10.10.0/24
          30 deny   10.20.20.0/24 -> 10.10.10.0/24
          40 deny   10.30.20.0/24 -> 10.10.10.0/24
          50 permit ip any any
```

2026-11-13 逐台实读复核（`--collect-baseline`，只读）：

- `BR-B-R3` 上联是 `Ethernet1/0`（`172.16.2.2/30`），不是 `Et2/0`；对端 `HQ-R1 Et2/0` 为 `172.16.2.1`。
  `HQ-R1 Et1/0` 为 `172.16.1.1/30`，对端 `BR-A-R2 Et1/0` 为 `172.16.1.2`。
- `WAN-IN` 在 `Et1/0`、`Et2/0` 入向各挂一次，序号为隐式 10/20/30/40/50；`show access-lists` 会显示序号与命中计数。
- 三台交换机 `Et0/0` 只有 `switchport trunk encapsulation dot1q` + `switchport mode trunk`，
  **没有显式的 `switchport trunk allowed vlan` 行**（即默认 1-4094）；`VLAN20` 当前没有接入端口。
- `BR-A-R2` 默认路由 `0.0.0.0/0 via 172.16.1.1`，`BR-B-R3` 默认路由 `0.0.0.0/0 via 172.16.2.1`
  （这两条是 `ROUTE-1`/`ROUTE-2` 的还原依据）。

### 1.3 测试前快照（每次至少 5 分钟）

**停止服务后再复制数据库**，避免 WAL 不一致：

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force .\backups | Out-Null

# 1) 配置与数据库
Copy-Item .\.env ".\backups\.env.$stamp"
Copy-Item .\data\ops.db ".\backups\ops.db.$stamp"
Copy-Item .\data\ops.db-wal ".\backups\ops.db-wal.$stamp" -ErrorAction SilentlyContinue
Copy-Item .\data\ops.db-shm ".\backups\ops.db-shm.$stamp" -ErrorAction SilentlyContinue
Copy-Item .\data\chroma ".\backups\chroma.$stamp" -Recurse -Force

# 2) 设备运行配置（只读，不写设备）
@'
import os, sys, pathlib
sys.path.insert(0, ".")
from project_config import load_project_env
load_project_env()
from control.backup_manager import backup_running_config
devices = [
    ("HQ-R1","192.168.249.11"), ("BR-A-R2","192.168.249.12"), ("BR-B-R3","192.168.249.13"),
    ("HQ-SW1","192.168.249.14"), ("BR-A-SW2","192.168.249.15"), ("BR-B-SW3","192.168.249.16"),
]
for name, ip in devices:
    r = backup_running_config({"device_name": name, "mgmt_ip": ip, "device_type": "cisco_ios"},
                              os.environ["DEVICE_USERNAME"], os.environ["DEVICE_PASSWORD"])
    print(name, r["status"], r.get("backup_path") or r.get("reason"))
'@ | .\.venv\Scripts\python.exe -
```

**强烈建议**：在 VMware 里对 EVE-NG 虚拟机打一个快照（如 `before-test-YYYYMMDD`）。
设备级恢复失败时，回滚快照是最可靠的兜底。

### 1.4 恢复基线

```powershell
# 1) 停服务（见 3.2）
# 2) 恢复数据库与知识库
Copy-Item ".\backups\ops.db.$stamp" .\data\ops.db -Force
Remove-Item .\data\ops.db-wal, .\data\ops.db-shm -ErrorAction SilentlyContinue
Remove-Item .\data\chroma -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item ".\backups\chroma.$stamp" .\data\chroma -Recurse -Force

# 3) 恢复设备配置（按第 10 节的逆操作逐项执行；或回滚 VMware 快照）
# 4) 启动并复核
python start.py --live
```

恢复完成的判定：6/6 设备 SSH 可登录、业务路径 ping 通、采集批次可查、诊断页可打开。

---

## 2. 测试总表

| 编号 | 测试 | 写业务库 | 写设备 | 启动入口 | 恢复方式 |
|---|---|---|---|---|---|
| T1 | 单元/静态检查 | 否 | 否 | `pytest`、`ruff` | 删除缓存 |
| T2 | 设备连接与台账 N1-N9 | 否 | 否 | SSH、ping、SQL | 无 |
| T3 | 自动采集 C1-C7 | 是（可隔离） | 否 | `collector.run_collect` | 删临时库/保留批次 |
| T4 | 知识库 K1-K6 | 是（Chroma） | 否 | `build_index` | 还原 Chroma |
| T5 | LLM/RAG L1-L7 | 是（诊断记录） | 否 | `/api/diagnose` | 无需恢复，保留审计 |
| T6 | 合成评测 D/E | 是（实验元数据） | 否 | `test_cases.compare_kb` | 临时库或还原 DB |
| T7 | 真实故障演练 P5 | 是 | **是** | `scripts/live_fault_experiments.py` 或人工终端 + 采集 | 逆操作 + 复核 |
| T8 | 终端写操作安全 O3 | 是 | 是（可只读验证） | `/api/v1/terminal` | 备份还原 |
| T9 | 变更单全流程 | 是 | 是 | `/changes` + API | 备份 + 逆操作 |
| T10 | 报告与前端 P3/P6 | 否 | 否 | 报告 API、`npm` | 保留构建产物 |
| T11 | 验收审计 | 否 | 否 | `scripts/verify_acceptance.py` | 无 |

---

## 3. 服务启停（Web 测试共用）

### 3.1 启动

```powershell
# Mock：不连设备、不采集，适合 UI 和单元测试
python start.py

# Live：连接真实设备；先确保 APP_AUTO_COLLECT=false，避免启动即采集
python start.py --live

# 只检查环境
python start.py --check
```

`APP_AUTO_COLLECT=true` 时，Live 启动会在后台触发一次全量采集。做故障注入测试前应设为
`false`，否则采集时机会不可控。

后台运行：

```powershell
$p = Start-Process -FilePath .\.venv\Scripts\python.exe `
  -ArgumentList "start.py","--live" `
  -RedirectStandardOutput .\reports\service.out.log `
  -RedirectStandardError  .\reports\service.err.log `
  -PassThru
$p.Id | Set-Content .\reports\service.pid
```

### 3.2 停止

```powershell
$svcPid = Get-Content .\reports\service.pid
Stop-Process -Id $svcPid -Force
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

前台运行时按 `Ctrl+C`，等待启动器完成清理。

### 3.3 登录与健康检查

```powershell
$env:APP_API_TOKEN = ((Get-Content .env | Where-Object { $_ -match '^APP_API_TOKEN=' }) -replace '^APP_API_TOKEN=','')
$h = @{ Authorization = "Bearer $env:APP_API_TOKEN" }

Invoke-RestMethod http://127.0.0.1:8000/api/health -Headers $h
# 浏览器登录：http://127.0.0.1:8000/login
```

---

## 4. T1 单元测试与静态检查

### 启动

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q collector control rag_engine knowledge_base ops_tools webapp
```

按主题单跑：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_collection_run.py tests\test_connectivity_semantics.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_change_workflow.py tests\test_terminal_execution_security.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_answer_validation.py tests\test_evaluation.py
```

### 判定

- `pytest` 全绿；`ruff` 无输出；`compileall` 退出码 0。
- 失败项先判断是否与本次改动相关；无关失败单独记录，不要顺手改。

### 恢复

测试不写业务库，但会生成缓存：

```powershell
Remove-Item .\.pytest_cache -Recurse -Force -ErrorAction SilentlyContinue
```

`data/pytest-tmp` 可能是运行前就存在的测试目录：只清理本次新增的内容，无法判断来源时保留，
不要为了“干净”删除已有数据。

---

## 5. T2 设备连接与台账（N1-N9）

### 启动：网络与端口

```powershell
ping 10.10.146.218
ping 192.168.249.11
Test-NetConnection 192.168.249.11 -Port 22
Test-NetConnection 10.10.146.218 -Port 32769
```

### 台账与设备事实

```powershell
@'
import sys
sys.path.insert(0, ".")
from collector import db
db.init_db()
for d in db.list_active_devices():
    print(d["device_name"], d["mgmt_ip"], d["device_type"], d["lifecycle_status"])
'@ | .\.venv\Scripts\python.exe -
```

N5/N6/N7/N8 用只读命令核对：

```powershell
@'
import os, sys
sys.path.insert(0, ".")
from project_config import load_project_env
load_project_env()
from netmiko import ConnectHandler

checks = {
    "192.168.249.14": ["show vlan brief"],
    "192.168.249.15": ["show vlan brief"],
    "192.168.249.16": ["show vlan brief"],
    "192.168.249.11": ["show ip route", "show access-lists", "ping 10.10.10.100 repeat 4",
                       "ping 10.20.10.11 repeat 4", "ping 10.30.10.11 repeat 4"],
}
for host, cmds in checks.items():
    c = ConnectHandler(device_type="cisco_ios", host=host,
                       username=os.getenv("DEVICE_USERNAME"),
                       password=os.getenv("DEVICE_PASSWORD"), timeout=10)
    for cmd in cmds:
        print(f"===== {host} :: {cmd}")
        print(c.send_command(cmd, read_timeout=20))
    c.disconnect()
'@ | .\.venv\Scripts\python.exe -
```

### 判定

| 指标 | 期望 |
|---|---|
| N1/N2/N3 | 3 路由器、3 交换机、3 终端 |
| N4 | 6/6 SSH 登录，提示符为 `#`，`show privilege` = 15 |
| N5 | 3/3 交换机存在 active VLAN 10/20/99/999 |
| N6 | BR-A-R2、BR-B-R3 有默认路由；HQ-R1 有到 10.20/16、10.30/16 的静态路由 |
| N7 | HQ-R1 存在 `WAN-IN`，应用在 Et1/0、Et2/0 入方向 |
| N8 | HQ-R1 → 10.10.10.100 / 10.20.10.11 / 10.30.10.11 成功率 100% |

N8 若首次 ping 丢包，先重试一次排除 ARP 预热；仍失败再查链路。

### 恢复

纯只读，无需恢复。若临时改过台账，按 `POST /api/v1/inventory/import` 或 UI 台账页面还原，
并确认 `devices` 表没有重复记录。

---

## 6. T3 自动采集（C1-C7）

### 启动：建议隔离库

```powershell
$env:SQLITE_DB_PATH = ".\data\test_collect.db"
Remove-Item .\data\test_collect.db* -Force -ErrorAction SilentlyContinue

.\.venv\Scripts\python.exe -m collector.run_collect --devices HQ-R1,BR-A-R2,HQ-SW1
```

正式演示采集（写 `data/ops.db`，保留为证据）：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices HQ-R1,BR-A-R2,BR-B-R3,HQ-SW1,BR-A-SW2,BR-B-SW3
```

### 判定

```powershell
@'
import sqlite3, os
p = os.environ.get("SQLITE_DB_PATH") or "./data/ops.db"
c = sqlite3.connect(p)
for row in c.execute("""SELECT run_id, status, started_at_utc, published_at_utc,
                               summary_json FROM collection_runs ORDER BY started_at_utc DESC LIMIT 3"""):
    print(row)
print("items:", c.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0])
print("by type:", c.execute("SELECT capture_type, COUNT(*) FROM collection_items GROUP BY capture_type").fetchall())
'@ | .\.venv\Scripts\python.exe -
```

- 采集摘要 `ssh_commands_failed=0`；每台网络设备 ≥ 4 类 `capture_type`。
- 不可达设备必须显示 `failed/unreachable`，不能沿用历史成功值。
- 成功与失败项都要有 `raw_blob_id` 或 `error_message`。

### 恢复

```powershell
Remove-Item Env:SQLITE_DB_PATH
Remove-Item .\data\test_collect.db* -Force -ErrorAction SilentlyContinue
```

正式批次是演示证据，**不要删除**；如需重做，先备份 `data/ops.db` 再清理旧批次。

---

## 7. T4 知识库（K1-K6）

### 启动

```powershell
# 查看状态
Invoke-RestMethod http://127.0.0.1:8000/api/v1/knowledge-base/status -Headers $h

# 重建索引
.\.venv\Scripts\python.exe -m knowledge_base.build_index --rebuild
```

检索抽检：

```powershell
@'
import sys
sys.path.insert(0, ".")
from knowledge_base.retriever import retrieve
for q in ["VLAN 10 不通", "OSPF 邻居起不来", "ACL 拦截业务流量", "接口 line protocol down"]:
    docs = retrieve(q, top_k=3)
    print(q, "->", [(d.get("id"), d.get("category")) for d in docs])
'@ | .\.venv\Scripts\python.exe -
```

### 判定

- 文章 ≥ 20，VLAN/路由/DHCP/ACL/接口故障每类 ≥ 3；
- 6 个领域查询中 ≥ 5 个 Top3 命中对应类别；
- 知识文章不包含测试问题原文或标准答案；
- 诊断记录能关联 `retrieval_queries/retrieval_hits`。

### 恢复

重建前已备份 `data/chroma`。异常时停服务，删除新目录后还原：

```powershell
Remove-Item .\data\chroma -Recurse -Force
Copy-Item ".\backups\chroma.$stamp" .\data\chroma -Recurse -Force
```

---

## 8. T5 大模型接入与 RAG（L1-L7）

### 启动

```powershell
# 最小真实调用
@'
import sys
sys.path.insert(0, ".")
from project_config import load_project_env
load_project_env()
from rag_engine.llm_client import chat_completion
text, elapsed_ms, params = chat_completion(
    [{"role": "user", "content": "仅回复 OK"}],
    response_format_json=False,
)
print(text, elapsed_ms, params)
'@ | .\.venv\Scripts\python.exe -
```

诊断接口：

```powershell
$body = @{
  question = "帮忙检查一下总部和分支A之间的网络现在是否正常。"
  use_kb   = $true
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/diagnose `
  -Headers $h -ContentType "application/json; charset=utf-8" -Body $body
```

注意 PowerShell 5 对中文 JSON 的编码：建议保存为 UTF-8 文件后用
`--data-binary "@body.json"` 的 curl，或直接用 Web 诊断页。

### 判定

```powershell
@'
import sqlite3
c = sqlite3.connect("./data/ops.db")
print(c.execute("""SELECT id, model_id, prompt_template_id, use_kb, response_time_ms,
                          total_response_time_ms, context_snapshot_id
                   FROM diagnosis_records ORDER BY id DESC LIMIT 3""").fetchall())
'@ | .\.venv\Scripts\python.exe -
```

- 6 字段齐全：现象、证据、原因、检查步骤、建议、复测；
- `evidence_used.observation_id` 只引用本次证据；
- 有知识库时原因包含 `kb_refs`；
- 证据不足场景 `missing_information` 非空且不编造端口；
- Prompt 中无明文密码、密钥、SNMP 社区串。

### 恢复

诊断记录属于审计证据，保留。若只想验证模型连通性，用最小调用，不写库。

---

## 9. T6 合成场景评测（D1-D9 / E1-E8）

`test_cases.compare_kb` 用隔离 scratch 库存放测试证据，但实验、用例、结果会写入当前主库。
避免污染 `data/ops.db` 的做法：

```powershell
$env:SQLITE_DB_PATH = ".\data\test_eval.db"
$env:CHROMA_DB_PATH = ".\data\chroma_eval"
Remove-Item .\data\test_eval.db* -Force -ErrorAction SilentlyContinue

.\.venv\Scripts\python.exe -m test_cases.compare_kb `
  --skip-kb-build `
  --output .\reports\compare_results.json
```

首次或知识库变更后，去掉 `--skip-kb-build`，让它重建评测用索引。

### 判定

- 10 个场景 × 有/无知识库 = 20 条结果；
- `same_evidence_all_cases=true`；
- 有知识库准确率 ≥ 80%，且 ≥ 无知识库；
- 有知识库平均可执行性 ≥ 0.75；
- 每条失败都有 `error_code/error_message`。

```powershell
@'
import sqlite3
c = sqlite3.connect("./data/test_eval.db")
print(c.execute("SELECT status, summary_json FROM experiment_runs ORDER BY created_at_utc DESC LIMIT 1").fetchone())
'@ | .\.venv\Scripts\python.exe -
```

### 恢复

```powershell
Remove-Item Env:SQLITE_DB_PATH
Remove-Item Env:CHROMA_DB_PATH
Remove-Item .\data\test_eval.db* -Force -ErrorAction SilentlyContinue
Remove-Item .\data\chroma_eval -Recurse -Force -ErrorAction SilentlyContinue
```

`reports/compare_results.json` 是评测证据，保留。

---

## 10. T7 真实故障注入演练（P5）

### 10.1 通用流程模板

每一类故障都按下面 8 步走，缺一步就不算完成：

```text
0 前置：确认服务 Live、APP_AUTO_COLLECT=false、设备 6/6 可 SSH
1 基线：备份运行配置 + 记录一条正常采集批次
2 注入：只改运行配置，不执行 write memory
3 验证：接口/路由/VLAN/ACL 状态变化 + 业务 ping 失败
4 采集：python -m collector.run_collect --devices <受影响设备>
5 诊断：Web 诊断页提问，记录 diagnosis_id、证据、建议
6 修复：人工终端或变更单执行逆操作，记录操作人
7 复测：从正确源设备 ping 目标，记录成功率（轮询，最长 75 秒）
8 恢复：确认配置回到基线（running-config 与备份逐行 diff）
```

**注入时不要 `write memory`。** 这样即使忘记逆操作，设备重载后也会回到 startup-config。
只有确认恢复完成并需要持久化时，才保存。

> 2026-11-13 起这 8 步由 `scripts/live_fault_experiments.py` 固化执行（见 §10.7），
> 上文人工步骤保留作为理解与兜底。
>
> 恢复期判定必须用轮询：IOSvL2 在 `no shutdown` 之后要等生成树大约 30 秒才把端口转入
> forwarding，复测 ping 每 5 秒一次、最长等 75 秒；`show interfaces trunk` 也要等 STP
> 收敛才会重新列出 trunk 端口，因此恢复期的 show 断言最多轮询 45 秒。
> 2026-11-13 首次实跑就因为只等了 11 秒，把已恢复的 LINK-1 误判成 `failed_recovery`。

**案例与故障点对照**（与 `scripts/live_fault_experiments.py` 的案例表一致；命令均在 config mode 下执行，
验证 ping 统一从 HQ-R1 发 `ping <目标> source 10.10.10.1 repeat 4`）：

| 案例 | 故障点（设备/接口） | 注入 | 逆操作 | 采集设备 |
|---|---|---|---|---|
| LINK-1 | BR-A-SW2 Ethernet0/0（上联 trunk） | `shutdown` | `no shutdown` | BR-A-R2, BR-A-SW2 |
| LINK-2 | BR-B-SW3 Ethernet0/0（上联 trunk） | `shutdown` | `no shutdown` | BR-B-R3, BR-B-SW3 |
| VLAN-1 | BR-A-SW2 Ethernet1/0（access 口） | `switchport access vlan 20` | `switchport access vlan 10` | BR-A-SW2 |
| VLAN-2 | BR-A-SW2 Ethernet0/0（trunk） | `switchport trunk allowed vlan remove 10` | `switchport trunk allowed vlan add 10`（可选再 `no switchport trunk allowed vlan`） | BR-A-SW2 |
| ROUTE-1 | BR-B-R3 默认路由 | `no ip route 0.0.0.0 0.0.0.0 172.16.2.1` | `ip route 0.0.0.0 0.0.0.0 172.16.2.1` | BR-B-R3 |
| ROUTE-2 | BR-A-R2 默认路由 | `no ip route 0.0.0.0 0.0.0.0 172.16.1.1` | `ip route 0.0.0.0 0.0.0.0 172.16.1.1` | BR-A-R2 |
| ACL-1 | HQ-R1 WAN-IN（Et1/0、Et2/0 入向） | `5 deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255` | `no 5` | HQ-R1 |
| ACL-2 | BR-A-R2 Ethernet1/0 入向 | 新建 `HQ-IN`（`10 deny ip 10.10.10.0 0.0.0.255 10.20.10.0 0.0.0.255` + `20 permit ip any any`）后 `ip access-group HQ-IN in` | 先 `no ip access-group HQ-IN in`，再 `no ip access-list extended HQ-IN` | BR-A-R2, HQ-R1 |
| NORMAL-1 | — | 无（对照） | 无 | 6 台全量 |
| INSUFFICIENT-1 | — | 无（对照，隔离库只采集 HQ-R1） | 无 | HQ-R1 |

第二案例的选择理由：`HQ-R1 Et1/0`、`Et2/0` 入向已经各挂了 `WAN-IN`，IOS 同一接口同方向只能有一个
IP ACL，再挂会顶掉基线，所以 ACL-2 落在 `BR-A-R2 Et1/0`（实测无 ACL）；`LINK-2`/`ROUTE-2`/`VLAN-2`
则让同一类故障在两个站点/两种机制上各出现一次，避免结论只对单个设备成立。

### 10.2 链路中断（LINK_DOWN）

故障点：`BR-A-SW2 Ethernet0/0`（到 BR-A-R2 的 trunk）。
管理口是 `Et3/0`/`Vlan999`，不受影响。

注入：

```text
configure terminal
interface Ethernet0/0
 shutdown
end
```

验证：

```text
show ip interface brief
show interfaces trunk
```

BR-A-PC 控制台（`telnet 10.10.146.218 32777`）：

```text
ping 10.10.10.100 -c 4
```

预期：trunk 端口 down，BR-A-PC 到 HQ-SRV 不通；HQ-R1、BR-A-R2 仍可 SSH。

采集与诊断：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices BR-A-R2,BR-A-SW2
```

诊断问题示例：“分支A路由器和交换机之间好像不通了，能帮忙看看是什么问题吗？”

修复与恢复：

```text
configure terminal
interface Ethernet0/0
 no shutdown
end
```

复核：

```text
show interfaces trunk
show vlan brief
```

BR-A-PC 控制台 `ping 10.10.10.100 -c 4` 恢复 100%。

注意：不要 shutdown `BR-A-R2 Ethernet0/0` 后忘记恢复；它承载 `.10/.20/.99` 三个子接口，
虽然管理口 `Et3/0` 仍在，但整站业务会中断。

**第二案例 `LINK-2`**：同样的注入换到 `BR-B-SW3 Ethernet0/0`（到 BR-B-R3 的 trunk）。
管理面仍在 `Et3/0`/`Vlan999`，不受影响。

```text
configure terminal
interface Ethernet0/0
 shutdown
end
```

逆操作：

```text
configure terminal
interface Ethernet0/0
 no shutdown
end
```

验证与复测（全程只需 SSH，不必登 VPCS 控制台）：

```text
BR-B-SW3# show ip interface brief      ! Ethernet0/0 为 administratively down
BR-B-SW3# show interfaces trunk        ! 不再出现 Et0/0
HQ-R1#    ping 10.30.10.11 source 10.10.10.1 repeat 4   ! 成功率 0%
```

恢复后同口径 ping 应为 100%（生成树收敛，最长等 75 秒），`show interfaces trunk` 重新出现 `Et0/0`。

采集与诊断：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices BR-B-R3,BR-B-SW3
```

诊断问题示例：“分支B的交换机和路由器之间好像断了，分支B终端访问总部一直失败，帮我诊断一下。”

### 10.3 VLAN 错配（VLAN_MISCONFIG）

故障点：`BR-A-SW2 Ethernet1/0`（VLAN10 接入口）改到 VLAN20。

注入：

```text
configure terminal
interface Ethernet1/0
 switchport access vlan 20
end
```

验证：

```text
show vlan brief
show running-config interface Ethernet1/0
```

预期：VLAN10 下不再有 Et1/0，BR-A-PC 失去 VLAN10 连通性。

采集与诊断：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices BR-A-SW2
```

诊断问题示例：“分支A的测试终端连不上同VLAN的其他设备，怀疑是VLAN配置问题。”

修复与恢复：

```text
configure terminal
interface Ethernet1/0
 switchport access vlan 10
end
```

复核 `show vlan brief` 中 VLAN10 重新包含 Et1/0，BR-A-PC ping 恢复。

**第二案例 `VLAN-2`**：在 `BR-A-SW2 Ethernet0/0` 的 trunk 上移除 VLAN10（不再动接入端口）。

```text
configure terminal
interface Ethernet0/0
 switchport trunk allowed vlan remove 10
end
```

验证：

```text
show interfaces trunk
show running-config interface Ethernet0/0
```

预期：`Vlans allowed on trunk` 中 `Et0/0` 的列表从 `1-4094` 变成 `1-9,11-4094`（基线没有显式
allowed vlan 行，所以注入后 IOS 会把列表显式写进运行配置）；HQ-R1
`ping 10.20.10.11 source 10.10.10.1 repeat 4` 成功率为 0%。

恢复：

```text
configure terminal
interface Ethernet0/0
 switchport trunk allowed vlan add 10
end
```

`add 10` 之后功能即恢复（trunk 重新放通 VLAN10，ping 最长等 75 秒），但 IOS 通常会把
`switchport trunk allowed vlan 1-4094` 显式留在运行配置里，与基线文本不一致。需要精确还原时追加：

```text
configure terminal
interface Ethernet0/0
 no switchport trunk allowed vlan
end
```

脚本把这一步作为**可选**清理步骤：命令被设备拒绝只记 WARN，不阻断恢复；最终运行配置与注入前
备份的文本差异由 config diff 如实记入报告。

`VLAN-1`（接入端口改 VLAN）与 `VLAN-2`（trunk 移除 VLAN）是两个独立案例，各自单独一轮注入；
不要在同一轮里同时改接入端口和 trunk，否则故障归因不清晰。

### 10.4 缺省路由丢失（DEFAULT_ROUTE_MISSING）

故障点：`BR-B-R3` 的默认路由。

注入：

```text
configure terminal
no ip route 0.0.0.0 0.0.0.0 172.16.2.1
end
```

验证：

```text
show ip route
show running-config | include ip route
```

预期：`Gateway of last resort is not set`，BR-B-PC 无法访问 HQ-SRV。

采集与诊断：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices BR-B-R3
```

诊断问题示例：“分支B的终端访问总部服务器一直超时，路由是不是有问题？”

修复与恢复：

```text
configure terminal
ip route 0.0.0.0 0.0.0.0 172.16.2.1
end
```

复核 `show ip route` 出现 `S* 0.0.0.0/0 via 172.16.2.1`，BR-B-PC ping 恢复。

**第二案例 `ROUTE-2`**：注入点在 `BR-A-R2`，删除的是它自己的默认路由（下一跳 `172.16.1.1`）。

```text
configure terminal
no ip route 0.0.0.0 0.0.0.0 172.16.1.1
end
```

逆操作：

```text
configure terminal
ip route 0.0.0.0 0.0.0.0 172.16.1.1
end
```

验证：

```text
BR-A-R2# show ip route     ! Gateway of last resort is not set，且没有 0.0.0.0/0
HQ-R1#   ping 10.20.10.11 source 10.10.10.1 repeat 4   ! 成功率 0%
```

这里**必须带 `source 10.10.10.1`**：用默认源发 ping 时源地址会取 `172.16.1.1`，而
`172.16.1.0/30` 在 BR-A-R2 上是直连网段，回程不依赖默认路由，ping 仍会成功从而掩盖故障。

采集与诊断：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices BR-A-R2
```

诊断问题示例：“分支A访问不了总部和外网的其他网段，但到分支B的路由看起来正常，什么情况？”

复核 `show ip route` 重新出现 `0.0.0.0/0`，同口径 ping 恢复 100%（最长等 75 秒）。

### 10.5 ACL 过度封锁（ACL_OVERBLOCK）

故障点：`HQ-R1` 的 `WAN-IN`，在现有 permit 之前插入 deny。

注入：

```text
configure terminal
ip access-list extended WAN-IN
 5 deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255
end
```

验证：

```text
show access-lists WAN-IN
```

预期：序号 5 的 deny 命中分支A到总部的流量，BR-A-PC 无法访问 HQ-SRV。

从 HQ-R1 用**默认源** ping BR-A-PC 仍可能通（源地址取 `172.16.1.1`，回程目的地址不落在
`10.10.10.0/24`）；带 `source 10.10.10.1` 时回包（`10.20.10.11 -> 10.10.10.1`）会命中该 deny，
ping 失败。因此不登 VPCS 控制台也能验证：

```text
HQ-R1# ping 10.20.10.11 source 10.10.10.1 repeat 4   ! 期望 0%
```

采集与诊断：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices HQ-R1
```

诊断问题示例：“分支A终端突然访问不了总部服务器了，之前一直是通的，怀疑是不是ACL改动导致的。”

修复与恢复：

```text
configure terminal
ip access-list extended WAN-IN
 no 5
end
```

复核：

```text
show access-lists WAN-IN
```

确认只剩原有 10/20/30/40/50 五条，BR-A-PC ping 恢复。

**注意**：不要删除整个 `WAN-IN` 或改 `permit ip any any`，那会掩盖原始基线并扩大影响面。

**第二案例 `ACL-2`**：在 `BR-A-R2 Ethernet1/0`（去 HQ 的接口）入向新建一个误拦截的 ACL `HQ-IN`。

不在 HQ-R1 上再挂一个新 ACL 的原因：`HQ-R1 Et1/0`、`Et2/0` 入向已经各挂了 `WAN-IN`，
IOS 同一接口同方向只能挂一个 IP ACL，再挂会把基线 ACL 顶掉、掩盖原始配置；`BR-A-R2 Et1/0`
2026-11-13 实测没有任何 ACL，适合作为第二故障点。

注入（分成两个配置会话，先建 ACL 再挂接口）：

```text
configure terminal
ip access-list extended HQ-IN
 10 deny ip 10.10.10.0 0.0.0.255 10.20.10.0 0.0.0.255
 20 permit ip any any
end
```

```text
configure terminal
interface Ethernet1/0
 ip access-group HQ-IN in
end
```

验证：

```text
BR-A-R2# show access-lists HQ-IN
BR-A-R2# show running-config interface Ethernet1/0    ! 含 ip access-group HQ-IN in
HQ-R1#   ping 10.20.10.11 source 10.10.10.1 repeat 4   ! 请求在 BR-A-R2 入向被丢弃，成功率 0%
```

逆操作（顺序不能反：先摘接口，再删 ACL）：

```text
configure terminal
interface Ethernet1/0
 no ip access-group HQ-IN in
end
```

```text
configure terminal
no ip access-list extended HQ-IN
end
```

复核：`show access-lists` 中不再有 `HQ-IN`，接口配置不再有 `ip access-group`，
同口径 ping 恢复 100%（最长等 75 秒）。

采集与诊断：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices BR-A-R2,HQ-R1
```

诊断问题示例：“分支A和总部之间突然只有单向业务异常，总部侧看起来正常，怀疑是分支A路由器上有ACL误拦截，帮我定位。”

### 10.6 每轮演练后的统一恢复检查

```powershell
@'
import os, sys
sys.path.insert(0, ".")
from project_config import load_project_env
load_project_env()
from netmiko import ConnectHandler

expect = {
    "192.168.249.11": ["show ip interface brief", "show access-lists WAN-IN"],
    "192.168.249.12": ["show ip route"],
    "192.168.249.13": ["show ip route"],
    "192.168.249.14": ["show interfaces trunk"],
    "192.168.249.15": ["show vlan brief", "show interfaces trunk"],
    "192.168.249.16": ["show vlan brief", "show interfaces trunk"],
}
for host, cmds in expect.items():
    c = ConnectHandler(device_type="cisco_ios", host=host,
                       username=os.getenv("DEVICE_USERNAME"),
                       password=os.getenv("DEVICE_PASSWORD"), timeout=10)
    print("="*20, host)
    for cmd in cmds:
        print(c.send_command(cmd, read_timeout=20))
    c.disconnect()
'@ | .\.venv\Scripts\python.exe -
```

再执行一次全量采集，确认 `ssh_commands_failed=0`、业务 ping 全部恢复。

### 10.7 固化复现（scripts/live_fault_experiments.py）

8 步模板已固化成脚本，10 个案例（8 故障 + 2 对照）内置，不再需要手工拼命令。

```powershell
# 列出全部案例 ID 与一句话描述
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --list

# 只打印将执行的命令，不连设备、不写库
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --case LINK-1 --dry-run
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --all --dry-run

# 执行单个案例 / 顺序执行全部案例（会真实改运行配置，先做 §1.3 快照）
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --case LINK-1
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --all

# 只读抓取 6 台设备基线（running-config + vlan brief + ip route）
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --collect-baseline
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --collect-baseline --devices HQ-R1,BR-A-SW2
```

案例 ID：`LINK-1/2`、`VLAN-1/2`、`ROUTE-1/2`、`ACL-1/2`、`NORMAL-1`、`INSUFFICIENT-1`。
评分期望词在 `test_cases/evaluation.py` 的 `LIVE_EXPECTED_BY_ID`，必须与脚本案例表一一对应
（脚本启动时会校验，缺一即退出码 2）。

每个注入案例的执行顺序：

```text
备份 running-config -> 注入前基线 ping -> 注入 -> show+ping 断言故障
-> collector.run_collect 采集 -> run_real_case（use_kb=True/False）
-> 逆操作修复 -> HQ-R1 源 ping 轮询复测 -> 恢复期 show 断言 + running-config 与备份 diff
```

产物：

| 内容 | 路径 |
|---|---|
| 注入前配置备份（每设备一份） | `reports/backups/fault_<ID>_<ts>/<device>.cfg` |
| 步骤日志（时间戳 + 每条断言结论） | `reports/fault_experiments_<ts>.log` |
| 汇总 JSON（注入/恢复、run_id、两次诊断） | `reports/fault_experiments_summary_<ts>.json` |
| 只读基线 | `reports/baselines/<device>.txt` |
| 证据不足案例的隔离库 | `data/fault_insufficient_<ts>.db` |

退出码：`0` 全部 `success`；`1` 有失败/`partial`/`skipped`；`2` 配置或安全检查未通过。

**人工演示模式（验收演示用）**：

```powershell
# 顺序演示 8 个注入案例（每例注入并验证故障后暂停，回车自动恢复）
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --demo

# 只演示指定案例
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --demo --case VLAN-2

# 只打印演示计划（不连设备、不改配置）
.\.venv\Scripts\python.exe scripts\live_fault_experiments.py --demo --dry-run
```

demo 只覆盖 8 个注入案例，`NORMAL-1` / `INSUFFICIENT-1` 不参与（不注入）。
每个案例的演示节奏：

```text
备份 -> 注入 -> 故障现象断言（show + 源 ping 0%）-> 打印横幅并暂停
[评委在 webapp：控制台总览 -> 设备资产 -> 手动采集 -> 诊断页提问 -> 看证据/检索依据/建议]
按 Enter -> 自动逆操作 -> 轮询复测 ping 100% -> 恢复期 show 断言 + config diff
-> 打印“已恢复” -> 菜单：Enter 下一案例 / 案例 ID 跳转 / q 退出
```

横幅里的入口默认 `http://127.0.0.1:8000`，取自 `APP_HOST` / `APP_PORT`：

| 用途 | 地址 |
|---|---|
| 控制台总览 | `GET /console/overview` |
| 设备资产 | `GET /console/assets` |
| 诊断页（自然语言提问、查看检索依据） | `GET /console/diagnosis` |
| 手动触发采集 | `POST /api/v1/collection-runs` |

演示话术建议（评委视角）：

1. **故障态**：横幅出现即表示注入已生效、断言已通过，设备此刻确实处于故障中；脚本在暂停期间不会再改动设备。
2. **证据**：先在总览/资产确认受影响设备与接口，再触发一次采集，强调“诊断只使用本次已发布的采集证据”。
3. **诊断**：在诊断页用案例问题提问，先看 `evidence`（采集事实）再看待检索依据与结论；需要时可对比关闭知识库的差异。
4. **人工确认**：由评委确认结论是否指向正确设备/接口/根因，演示中不要求真正下发修复。
5. **自动恢复**：回车后脚本执行案例表里的逆操作，再轮询复测并逐行 diff；终端会打印“已恢复：复测 ping 100%，running-config 与备份一致”。
6. **收尾**：`q` 退出时网络已恢复；暂停期间按 Ctrl+C 也会先恢复当前案例再退出，并提示“已恢复，退出演示（无遗留故障）”。

demo 的 summary 同样写入 `reports/fault_experiments_summary_<ts>.json`，顶层 `mode="demo"`，
并记录每例 `pause_seconds`、`fault_window_s`（注入完成到开始恢复）、
`fault_duration_s`（该窗口减去暂停时长），`demo.pause_seconds_total` 是整场暂停总时长。

安全机制（改动脚本前先读一遍）：

- 拒绝 `write memory` / `copy run start` / `write startup-config` / `reload`，也拒绝以
  `Ethernet3/0`、`Vlan999` 管理面为目标的命令；案例表在连设备之前就做这项校验。
- 逆操作放在 `finally`；`repair` 失败或恢复未确认时状态为 `failed_recovery`，`--all`
  立即停止后续案例，避免带着未恢复的故障继续注入。
- 恢复判定用轮询：ping 每 5 秒一次、最长 75 秒；恢复期 show 断言每 5 秒一轮、最长 45 秒
  （生成树约 30 秒才收敛）。
- 日志只写命令与断言结论，不写口令，也不写 `show running-config` 全文；config diff 中含
  `secret`/`password`/`community` 的行只计数、不输出。
- 单案例失败不中断 `--all`，但恢复失败会硬停；`--dry-run` 完全不连设备。

断言解析与案例表的单测在 `tests/test_live_fault_experiments.py`（不连设备，覆盖 VLAN 端口归属、
trunk allowed 列表、`administratively down` 行匹配、ping 成功率解析、轮询收敛与超时）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_live_fault_experiments.py
```

### 10.8 2026-11-13 实机执行记录

环境：Windows 主机 + 项目 `.venv`，经 ZeroTier 访问 EVE-NG 管理地址 `10.10.146.218`，
6/6 Cisco 设备 SSH 可达；执行期间 `APP_AUTO_COLLECT=false`，全程未执行 `write memory`。

- 10 个案例全部执行：9 例首轮 `success`；`LINK-1` 首轮在修复后 11 秒复测仍 0% 被判
  `failed_recovery`（当时只等 2 秒 + 一次复测，而生成树约 30 秒才 forwarding），改为
  每 5 秒轮询、最长 75 秒后重跑通过。`--all` 的硬停止按设计生效，没有在未确认恢复时继续注入。
- `fault_injections` 共 10 条 `injection_mode=live`（8 个故障案例；`LINK-1`、`ROUTE-2` 因复跑各多
  一条，状态都停留在 `injected`，见 §18.1）；每例都完成
  备份 -> 注入 -> 现象断言 -> 采集 -> 有/无 KB 双诊断 -> 逆操作 -> 源 ping 复测
  -> running-config 逐行 diff（8 例全部一致，0 删除 / 0 新增）。
- 逐案例结果（每案例取最后一次成功运行；`LINK-1`、`ROUTE-2` 两次都列出）：

| 案例 | 注入 / 逆操作 | 故障断言 | 诊断（有 KB / 无 KB） | 恢复 |
|---|---|---|---|---|
| LINK-1（首轮 08:06） | BR-A-SW2 Et0/0 shutdown / no shutdown | Et0/0 administratively down、trunk 消失、ping 0% | correct 0.77 / partial 0.53 | 复测误判 `failed_recovery`；配置 diff 一致 |
| LINK-1（重跑 08:27） | 同上 | 同上 | partial 0.53 / correct 0.77 | success，ping 100%，diff 一致 |
| LINK-2 | BR-B-SW3 Et0/0 shutdown / no shutdown | 同上（BR-B 侧） | correct 0.85 / partial 0.62 | success，ping 100%，diff 一致 |
| VLAN-1 | BR-A-SW2 Et1/0 → VLAN20 / 改回 VLAN10 | VLAN10 无 Et1/0、VLAN20 有、ping 0% | correct 0.77 / correct 1.00 | success，diff 一致 |
| VLAN-2 | BR-A-SW2 Et0/0 移除 VLAN10 / add 10 | trunk allowed 不含 10、ping 0% | partial 0.65 / correct 1.00 | success，diff 一致 |
| ROUTE-1 | BR-B-R3 删默认路由 / 恢复 172.16.2.1 | 无 0.0.0.0/0、ping 0% | partial 0.65 / wrong 0.30 | success，ping 100%，diff 一致 |
| ROUTE-2 | BR-A-R2 删默认路由 / 恢复 172.16.1.1 | 无 0.0.0.0/0、ping 0% | correct 1.00 / correct 1.00 | success，diff 一致 |
| ROUTE-2（重跑 08:30） | 同上 | 同上 | correct 1.00 / wrong 0.30 | success，diff 一致 |
| ACL-1 | HQ-R1 WAN-IN 插入 5 deny / no 5 | seq 5 deny 可见、ping 0% | correct 1.00 / correct 1.00 | success，diff 一致 |
| ACL-2 | BR-A-R2 Et1/0 挂 HQ-IN / 摘接口+删 ACL | ACL 与挂载可见、ping 0% | correct 1.00 / correct 1.00 | success，diff 一致 |
| NORMAL-1 | 无（全量采集） | 6/6 采集、ping 100% | correct 1.00 / correct 1.00 | 无需恢复 |
| INSUFFICIENT-1 | 无（隔离库只采集 HQ-R1） | BR-B-SW3 证据为 `no_data` | correct 1.00 / correct 1.00 | 无需恢复 |

- 评测汇总（主库 `experiment_results` 22 条 = 11 次运行 × 2 变体；`LINK-1`、`ROUTE-2` 各两次，
  `INSUFFICIENT-1` 在隔离库不计入）：
  - `with_kb`：correct 8 / partial 3（LINK-1 重跑、VLAN-2、ROUTE-1）/ wrong 0，均值分 0.838；
  - `without_kb`：correct 7 / partial 2（LINK-1 首轮、LINK-2）/ wrong 2（ROUTE-1、ROUTE-2 重跑），
    均值分 0.774；
  - 可执行性均 1.0；响应 `with_kb` 30.6–39.7s（均值 34.2s）、`without_kb` 21.3–41.9s（均值 32.5s）；
    `INSUFFICIENT-1` 证据极少，仅 5–7s。
- 口径说明：同一案例复跑会在 `experiment_results` 里重复累计。按“每案例最后一次运行”去重
  （10 例，含隔离库的 `INSUFFICIENT-1`）是 `with_kb` 7 correct / 3 partial、
  `without_kb` 7 correct / 1 partial / 2 wrong；按主库全量记录则是 `with_kb` 8/3/0、
  `without_kb` 7/2/2。复跑评测一律以 `experiment_results` 全量记录为准，单次运行只作过程证据
  （日志与 summary JSON 见 `reports/fault_experiments_<ts>.log` / `..._summary_<ts>.json`，
  首轮失败记录为 `reports/fault_experiments_20261113T080634.log`）。
- **KB 效果不能只看单次**：`LINK-1` 两次运行结论相反（首轮有 KB correct、重跑无 KB correct），
  `ROUTE-2` 重跑时无 KB 反而判 wrong；`qwen-plus` 在 `temperature=0.2` 下仍有波动，
  评测结论应取多次运行均值，并对相同证据的两变体做配对比较。
- 已知遗留问题（VLAN-2 证据链缺 trunk 明细、`fault_injections.status` 未回写、
  `INSUFFICIENT-1` 使用隔离库）见 §18。

---

## 11. T8 人工终端与写操作安全（O3）

### 启动

先创建会话，必须显式确认已了解备份要求：

```powershell
$s = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/terminal/sessions `
  -Headers $h -ContentType "application/json" `
  -Body (@{ device_name="BR-A-R2"; operator="admin"; backup_acknowledged=$true } | ConvertTo-Json)

$sid = $s.id

# 实际备份（不是勾选确认）；该接口不接收请求体
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/terminal/sessions/$sid/backup" `
  -Headers $h
```

只读命令：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/terminal/sessions/$sid/commands" `
  -Headers $h -ContentType "application/json" `
  -Body (@{ command="show ip interface brief"; confirmed=$true } | ConvertTo-Json)
```

写命令令牌：

```powershell
$intent = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/terminal/sessions/$sid/write-intents" `
  -Headers $h -ContentType "application/json" `
  -Body (@{ command="interface Ethernet0/0" } | ConvertTo-Json)
```

### 判定（负向）

| 场景 | 期望 |
|---|---|
| 未 `backup_acknowledged` 开会话 | 409 `BACKUP_ACKNOWLEDGEMENT_REQUIRED` |
| 未实际备份就取写令牌 | 409 `TERMINAL_BACKUP_REQUIRED` |
| 写命令不带 `confirmation_id` | 拒绝 |
| 令牌用于不同命令或不同会话 | 拒绝 |
| 所有命令 | 进入审计事件，输出脱敏 |

### 恢复

关闭会话：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/terminal/sessions/$sid/close" `
  -Headers $h
```

若测试中改了配置，按第 10 节对应逆操作恢复；必要时用 `reports/backups/` 的备份人工比对。

---

## 12. T9 变更单全流程

入口：`http://127.0.0.1:8000/changes`，或 `/api/changes`。

### 12.1 创建

变更必须关联一次诊断结论，先取 `diagnosis_id`（也可以直接用已有诊断记录）：

```powershell
$diag = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/diagnose `
  -Headers $h -ContentType "application/json" `
  -Body (@{ question="分支A到总部的链路中断了，请给出恢复建议。"; use_kb=$true } | ConvertTo-Json)

$c = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/changes `
  -Headers $h -ContentType "application/json" `
  -Body (@{
    diagnosis_id  = $diag.diagnosis_id
    device_name   = "BR-A-SW2"
    change_type   = "software"
    title         = "恢复 BR-A-SW2 Et0/0 trunk"
    description   = "故障注入后恢复链路"
    risk_level    = "medium"
    operator      = "admin"
    rollback_plan = "若 no shutdown 后仍 down，回滚 VMware 快照并重新采集"
  } | ConvertTo-Json)

$changeId = $c.change_id
```

### 12.2 加步骤、审批、备份

```powershell
$step = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/changes/$changeId/steps" `
  -Headers $h -ContentType "application/json" `
  -Body (@{
    step_type      = "software"
    source_type    = "human"
    title          = "恢复 trunk 端口"
    command_text   = "interface Ethernet0/0`n no shutdown"
    expected_result= "trunk 恢复 up，VLAN10/20/99/999 全部放通"
    operator       = "admin"
  } | ConvertTo-Json)

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/changes/$changeId/approve" `
  -Headers $h -ContentType "application/json" `
  -Body (@{ approver="lead"; decision="approved"; comment="实验环境恢复" } | ConvertTo-Json)

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/changes/$changeId/backup" `
  -Headers $h -ContentType "application/json" -Body (@{ operator="admin" } | ConvertTo-Json)
```

### 12.3 执行、复测、完成

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/changes/$changeId/steps/$($step.step_id)/execute" `
  -Headers $h -ContentType "application/json" `
  -Body (@{
    command_text   = "interface Ethernet0/0`n no shutdown"
    output         = "BR-A-SW2(config-if)# no shutdown"
    exit_code      = 0
    result_summary = "端口恢复"
    operator       = "admin"
  } | ConvertTo-Json)

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/changes/$changeId/recheck" `
  -Headers $h -ContentType "application/json" `
  -Body (@{
    source_device_name = "HQ-R1"
    target_ip          = "10.20.10.11"
    expected_reachable = $true
    operator           = "admin"
  } | ConvertTo-Json)

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/changes/$changeId/complete" `
  -Headers $h -ContentType "application/json" `
  -Body (@{ operator="admin"; outcome="completed" } | ConvertTo-Json)
```

### 12.4 回滚

设备侧先执行逆操作，再把变更单收口为 `rolled_back`：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/changes/$changeId/complete" `
  -Headers $h -ContentType "application/json" `
  -Body (@{ operator="admin"; outcome="rolled_back" } | ConvertTo-Json)
```

`outcome` 只接受 `completed`、`failed`、`rolled_back`。回滚不是自动执行配置，配置恢复仍由人工完成。

### 判定

- 未审批不能备份/执行；
- 未备份不能执行写步骤；
- 复测结果绑定 `collection_run_id` 和 `evidence_observation_id`；
- 事件流包含创建、审批、备份、执行、复测、完成/回滚。

---

## 13. T10 报告导出与前端（P3/P6）

```powershell
# Markdown
Invoke-WebRequest "http://127.0.0.1:8000/api/v1/sessions/<session_id>/report" -Headers $h -OutFile .\reports\session.md

# HTML
Invoke-WebRequest "http://127.0.0.1:8000/api/v1/sessions/<session_id>/report?format=html" -Headers $h -OutFile .\reports\session.html

# 非法格式应 400
curl.exe -s -o NUL -w "%{http_code}" -H "Authorization: Bearer $env:APP_API_TOKEN" `
  "http://127.0.0.1:8000/api/v1/sessions/<session_id>/report?format=pdf"
```

前端：

```powershell
Set-Location .\frontend
npm test
npm run build
Set-Location ..
```

E2E（需要服务已启动）：

```powershell
Set-Location .\frontend
npm run test:e2e
Set-Location ..
```

### 恢复

`dist/`、`test-results/`、`playwright-report/` 是构建和测试产物。除非要清理工作区，
否则保留；`git status` 里出现的临时产物按 `.gitignore` 处理。

---

## 14. T11 验收审计

只读审计，不写数据库：

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_acceptance.py `
  --db .\data\ops.db `
  --articles .\knowledge_base\articles.json `
  --reports .\reports `
  --json-output .\reports\acceptance_audit.json
```

退出码为 0 表示没有 `FAIL`；有 `FAIL` 时逐项查看输出中的编号和实测值。
审计结果只反映当前库里的证据，不能用旧批次或预置文本替代。

---

## 15. 故障与回滚矩阵

| 现象 | 先检查 | 处理 |
|---|---|---|
| SSH 登录失败 | `Test-NetConnection <ip> -Port 22`、`DEVICE_USERNAME/PASSWORD` | 不要怀疑 enable；检查账号和 ACL |
| 登录后停在 `>` | 账号权限是否被改 | 用 privilege 15 账号，或从控制台提权 |
| 采集 0 条 | `collection_runs` 是否创建、`APP_AUTO_COLLECT` | 手动 `run_collect`，看日志 |
| 诊断无证据 | 最新批次是否发布、证据时间 | 重新采集；不要用历史状态补齐 |
| 知识库检索为空 | `knowledge-base/status`、Chroma 路径 | 重建索引或还原 Chroma |
| LLM 超时 | `LLM_*` 配置、网络、额度 | 用最小调用定位，不要让诊断链路重试风暴 |
| 故障注入后设备失联 | 是否 shutdown 了管理口 | 走 EVE 控制台恢复 `no shutdown` |
| 刚 `no shutdown` 后仍 ping 不通 | 端口是否已从 STP 转入 forwarding（约 30 秒） | 等轮询窗口（每 5 秒、最长 75 秒）；不要立刻判恢复失败 |
| 恢复后仍不通 | 接口/VLAN/路由/ACL 四项逐项比对基线 | 按 10.6 全量复核，必要时回滚快照 |
| 测试污染了 ops.db | 是否有测试前备份 | 停服务，还原 `ops.db` 并删除 `-wal/-shm` |
| 端口 8000 被占用 | `Get-NetTCPConnection -LocalPort 8000` | 停旧进程或改 `APP_PORT` |

---

## 16. 测试后清理清单

- [ ] 服务已停止，8000 端口无监听
- [ ] 临时库 `test_collect.db`、`test_eval.db` 已删除，相关环境变量已清除
- [ ] 设备配置已按 10.6 复核；用脚本跑过的话确认每例 `recovery_ok=true`、`config_restored=true`，业务 ping 全部恢复
- [ ] `data/fault_insufficient_*.db` 是 INSUFFICIENT-1 的证据库：归档或按需保留，不要当成临时库删掉
- [ ] 未保存故障配置到 startup-config；如已保存，确认恢复后再保存
- [ ] `reports/` 中保留本次诊断、评测、审计证据
- [ ] `git status` 无意外新增文件；测试缓存和临时产物已清理
- [ ] 若使用了 VMware 快照，确认无需回滚后再删除快照

---

## 17. 单轮执行记录模板

```text
测试编号:
测试目的:
开始时间:
操作人:
基线备份: ops.db=..., chroma=..., 设备配置=..., 快照=...
注入动作: 设备/接口/命令
故障证据: 采集批次 run_id=..., 关键输出=...
诊断记录: diagnosis_id=..., 模型=..., 是否用知识库=...
修复动作: 变更单/终端会话=..., 命令=...
复测结果: 源=..., 目标=..., 成功率=...
恢复确认: 接口=..., 路由=..., VLAN=..., ACL=..., ping=...
遗留问题:
结束时间:
```

---

## 18. 已知边界

- 采集计划页面只保存配置，不会自动调度；定时采集需外部调度器调用
  `collector.run_collect --trigger scheduled`。
- `test_cases.real_evaluation` 的命令行入口只内置了正常场景的 `expected`；真实故障案例已
  固化成 `scripts/live_fault_experiments.py`（10 例 + 8 步流程，见 §10.7），期望词在
  `test_cases/evaluation.py` 的 `LIVE_EXPECTED_BY_ID`。需要单条复跑时也可以直接调用
  `run_real_case(..., expected=...)`。
- `test_cases.demo_e2e` 使用隔离临时库，但会写 `reports/demo_e2e_*.md`；它不覆盖
  `data/ops.db`。
- 设备 `enable secret` 存在但未知；SSH 账号是 privilege 15，采集与排障不需要它。
  如必须提权控制台，优先从现有 SSH 会话重设，不要做 ROMMON 密码恢复。
- 交换机管理地址在 `Vlan999`，路由器在 `Ethernet3/0`；故障注入一律避开这两个接口。
- `network/device_inventory.json` 的 `interfaces` 仍写作 `GigabitEthernet0/x`，真机是
  `Ethernet0/x`；脚本不依赖该字段，但台账应与真机一起修正。

### 18.1 遗留问题（2026-11-13 实机执行后）

1. **VLAN-2 的 trunk 明细进不了证据链。** `collector/state_parser.py` 的 `parse_config()`
   只产出 `line_count + sha256`，`evidence_builder` 又把它摘要成“配置快照 N 行”，
   `switchport trunk allowed vlan` 的具体列表从未进入提示词。结果是该案例即使有 KB 也
   只判 `partial`（0.65）。修法二选一：把 `show interfaces trunk` 加进采集白名单
   （`capture_type=vlan`），或让 config 证据带上接口配置段落摘录。
2. **`fault_injections.status` 恢复后仍停留 `injected`。** `run_real_case` 注册时写死
   `status="injected"`，项目里没有“恢复后置为 verified/recovered”的 DB 接口；复跑还会再插
   一条，所以 2026-11-13 的 8 个故障案例留下了 10 条 `live` 记录。当前以
   `fault_experiments_summary_<ts>.json` 的 `repair.ok` / `config_restored` 作为恢复依据；
   后续应补更新函数并在修复步骤后调用。
3. **`INSUFFICIENT-1` 使用隔离库。** 该案例的 case/experiment/diagnosis 行写在
   `data/fault_insufficient_<ts>.db`（这样 BR-B-SW3 才是真正的 `no_data`），不在
   `data/ops.db`。统计时要把两库一起算，或后续改成“主库 + 显式 `no_data` 构造”。
4. **LLM 输出有波动。** 同一案例两次运行可能得出相反的 KB 结论（见 §10.8），
   单次结果不足以证明 KB 增益，评测应取多次运行均值。
