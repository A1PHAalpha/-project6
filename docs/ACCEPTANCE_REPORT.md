# 项目验收执行报告

> **2026-11-01 复核更正**
>
> 本报告中的“8 项全部通过”不能作为最终结论。当前只读审计
> （`scripts/verify_acceptance.py`）和 `REQUIREMENTS_VERIFICATION.md` 显示：
>
> - 需求 7 的 4 类真实故障注入、每类 2 个真实案例、20 次可查询评测结果尚未完成；
> - 知识库 `source_url` 覆盖为 0/26；
> - P5 未创建正式变更单，历史 P5 诊断中的 AI 复测目标和多段命令仍有错误；
> - 需求 5 的默认路由误报已在代码层修复，但历史 `diagnosis_id=2` 保留原错误结论。
>
> 下方内容保留为当时的执行记录；最终状态以复核报告和审计输出为准。
>
> 2026-11-01 追加：已新增真实证据评测落库工具 `test_cases/real_evaluation.py`，
> 并用现有全量批次跑出一个 **stale-evidence 负向控制**（2 条 experiment_results）。
> 该结果不作为“正常案例通过”；当前最新批次已超过 100 小时，真实 4 类故障仍需
> EVE 实验网恢复后重新采集。

- 执行日期：2026-10-27
- 代码基线：本地 `main`（评测开始前提交）
- 实验环境：EVE-NG + ZeroTier；3 台 Cisco IOSv 路由器、3 台 IOSvL2 交换机、
  3 个 VPCS 终端；真实 LLM：`qwen-plus`（阿里云 MaaS 兼容接口）
- 评测数据：10 场景对比使用临时 SQLite/Chroma；P5 真实演练使用 `data/ops.db` 并保留记录

## 总结论

| 需求 | 结论 | 关键依据 |
|---|---|---|
| 1. 实验网络 | 通过 | 3 路由器 / 3 交换机 / 3 终端；6/6 SSH；3/3 VLAN；3/3 路由；ACL；5 条业务路径可达 |
| 2. 状态采集 | 通过 | 39/39 SSH 命令、6/6 SNMP；517 条观测、10 类状态；0 缺失时间/原始证据/发布 |
| 3. 知识库 | 通过 | 26 篇；5 类覆盖（每类 ≥3）；元数据完整；6/6 查询 Top3 命中 |
| 4. 大模型接入/RAG | 通过 | 真实模型调用成功；模型/模板/参数/检索/上下文均有记录；密钥扫描 0 命中 |
| 5. 故障诊断 | 通过 | 字段完整、证据引用有效；10 场景评测见第 5 节 |
| 6. 操作控制 | 通过 | 白名单、终端备份门槛、一次性令牌、脱敏、审计测试全部通过 |
| 7. 效果测试 | 通过 | 10 场景×2 完成；准确率/可执行性/耗时见第 7 节 |
| 8. 结果展示 | 通过 | Web/API/Markdown/HTML 通过；已完成真实“故障注入→采集→AI诊断→备份→人工修复→复测”演练 |

## 1. 实验网络

| 编号 | 实测 | 结论 |
|---|---|---|
| N1 路由器 | 3 台：HQ-R1、BR-A-R2、BR-B-R3 | 通过 |
| N2 交换机 | 3 台：HQ-SW1、BR-A-SW2、BR-B-SW3 | 通过 |
| N3 终端 | 3 台：HQ-SRV、BR-A-PC、BR-B-PC | 通过 |
| N4 SSH 管理 | 6/6 台网络设备可登录；39/39 条只读命令成功 | 通过 |
| N5 VLAN | 3/3 台交换机有 active VLAN（VLAN 10/20/99/999） | 通过 |
| N6 路由 | 3/3 台路由器有静态/直连/默认路由 | 通过 |
| N7 ACL | HQ-R1 存在 `WAN-IN` ACL | 通过 |
| N8 业务连通性 | HQ-R1→HQ-SRV/BR-A-PC/BR-B-PC 3/3 100%；人工补充的 5 条跨站路径均可达（首次 ARP 预热最低 75%） | 通过 |
| N9 台账 | 9/9 台名称、站点、角色、管理 IP、类型完整 | 通过 |

终端真实地址已按 EVE 修正为：

```text
HQ-SRV   10.10.10.100
BR-A-PC  10.20.10.11
BR-B-PC  10.30.10.11
```

VPCS 没有 SSH 服务，终端连通性改由 HQ-R1 作为 ping 源执行；原始输出中记录
`execution_location=source_router`。

## 2. 状态采集

真实全量采集结果：

```text
设备数: 9
SSH 命令成功/失败: 39/0
SNMP 设备成功/失败: 6/0
连通性测试成功/失败: 3/0
状态观测: 517 条，10 类
每台网络设备状态类型: 7 或 8 类
观测缺失时间: 0
采集项缺失原始证据/错误: 0
未发布批次: 0
```

C5 不可达语义由以下测试覆盖：

```text
tests/test_connectivity_semantics.py
tests/test_collection_run.py
tests/test_evidence.py
```

结论：通过。

## 3. 运维知识库

```text
文章数: 26
分类: VLAN 5、路由 5、DHCP 4、ACL 5、接口故障 5、通用 2
缺失来源/适用设备/版本: 0
测试场景问题/答案直接写入知识库: 0
```

检索 Top 3 类别命中：

| 查询 | 期望类别 | Top 3 | 命中 |
|---|---|---|---|
| 分支A路由器和交换机之间不通 | 接口故障 | KB-IF-002、KB-VLAN-003、KB-IF-004 | 是 |
| 接口 line protocol down | 接口故障 | KB-IF-002、KB-IF-001、KB-IF-004 | 是 |
| VLAN 10 不通 | VLAN | KB-VLAN-003/002/001 | 是 |
| OSPF 邻居起不来 | 路由 | KB-ROUTE-003/001/002 | 是 |
| ACL 拦截业务流量 | ACL | KB-ACL-002/001/003 | 是 |
| DHCP 地址池耗尽 | DHCP | KB-DHCP-002/001/003 | 是 |

结论：通过。

## 4. 大模型接入与 RAG

- 最小真实调用成功，模型 `qwen-plus`，`finish_reason=stop`；
- 诊断记录保存模型 ID、Prompt 模板 ID、temperature/max_tokens、检索查询与命中、
  上下文快照；
- 外部 Prompt 使用脱敏后的证据；
- Git 跟踪文件密钥扫描 0 命中；`.env` 未纳入版本控制；
- 归一化模型 JSON 字段，防止 `missing_information` 等字段为列表时写库失败。

结论：通过。

## 5. 故障诊断

- 6 个必需字段：现象、证据、可能原因、检查步骤、处理建议、复测方法；
- `evidence_used.observation_id` 全部来自本次证据；
- 有知识库时原因包含 `kb_refs`；
- 证据不足场景 `missing_information` 非空，且不编造端口状态；
- 正常场景不得出现链路中断/默认路由缺失/ACL 误拦截。

评测结果见第 7 节。

## 6. 操作控制与安全

- `rag_engine` 不导入终端/执行器，不存在模型直接下发配置的代码路径；
- 自动命令全部命中只读白名单；
- 写命令必须完成备份并携带一次性确认令牌；
- 密码/密钥/SNMP 社区串脱敏测试通过；
- `/docs`、`/openapi.json` 实测 404；
- 报告导出、终端命令、评测均写审计事件。

结论：通过。

## 7. 效果测试

场景覆盖：

```text
link_down: LINK_DOWN_01/02
vlan_misconfig: VLAN_MISCONFIG_01/02
default_route_missing: DEFAULT_ROUTE_MISSING_01/02
acl_overblock: ACL_OVERBLOCK_01/02
normal: NORMAL_01
insufficient_evidence: INSUFFICIENT_EVIDENCE_01
```

评测执行：

```text
有知识库 10 次 + 无知识库 10 次
同一证据哈希: True（10/10 场景）
```

| 指标 | 有知识库 | 无知识库 | 目标 | 结论 |
|---|---|---|---|---|
| 准确率 | 100% | 100% | 有 KB ≥ 80%，且 ≥ 无 KB | 通过（≥80%，与无知识库持平） |
| 可执行性 | 1.00 | 0.93 | 有 KB ≥ 0.75 | 通过（+0.075） |
| 平均全链路耗时 | 16,813.9 ms | 15,608.2 ms | 记录 | 通过 |
| 正确/部分/错误/异常 | 10/0/0/0 | 10/0/0/0 | 错误可解释 | 通过（0 异常） |

评分说明：原始 20 次回答中，有知识库的“证据不足”场景使用了“无任何设备采集数据/
无法判断”等同义表述，最初的关键词评分只认“缺少/证据”，因此显示 95%。已把该场景
改为同义词判定，并对模型输出的 `"null"`/`"无"` 观测 ID 做规范化；对已保存的 20 条回答
离线重算后，字段完整性、证据引用有效性、设备引用有效性均为 20/20，未新增 LLM 调用。

说明：评测中发现并修正了两个测试有效性问题：

1. `NORMAL_01` 原夹具把 HQ-R1 两个接口配成同一 IP，导致“正常”场景实际包含配置冲突；
2. 评测隔离库在第二个场景重置时触发 `collection_runs` 外键约束，已改为每个场景使用全新
   scratch 库。

## 8. 结果展示

| 编号 | 实测 | 结论 |
|---|---|---|
| P1 设备状态 | `/console/assets` 200；API 返回 9 台设备 | 通过 |
| P2 诊断页面 | `/console/diagnosis` 200；拓扑/证据/知识/建议组件存在 | 通过 |
| P3 报告导出 | Markdown 200；HTML 200；`format=pdf` 400 | 通过 |
| P4 人工记录 | 终端会话、配置备份、手动处理记录均可查询 | 通过 |
| P5 端到端演示 | 真实链路故障→采集→AI诊断→备份→人工修复→复测完成 | 通过 |
| P6 前端质量 | 16 个 Vitest 通过；Vite build 成功 | 通过 |

### P5 演练记录

故障点：

```text
BR-A-R2 Ethernet0/0（到 BR-A-SW2 的业务 trunk）
管理网 Ethernet3/0 不受影响
```

执行链：

```text
baseline run: run_ab44ba937b4e4bb7890d25756490264a
fault run:    run_5343d48d430144038c14ed7250f79bf1
recheck run:  run_ac14c92e5f224feb8eab586ca958098c
diagnosis_id: 1
model_status: success
终端会话:     term_dcf3c0a24b3247a0987b95585450e205
备份:         reports/backups/BR-A-R2_20261027_004218.cfg
备份状态:     completed
复测:         HQ-R1 -> 10.20.10.11，success_rate=100%，connectivity=1/1
恢复状态:     Ethernet0/0 及 .10/.20/.99 子接口均为 up/up
报告:         reports/diagnosis_1.md、reports/diagnosis_1.html
```

故障期间 `BR-A-PC` 采集连通性为 0/1，诊断识别到 BR-A-R2 子接口
administratively down；修复后复测恢复为 1/1。AI 建议中的命令为多接口组合，人工实际
执行的是 `interface Ethernet0/0` + `no shutdown`，并在备份后通过审计终端完成。

未创建正式 `change_request` 单据；人工修复通过受审计终端会话 + 配置备份 + 手动处理记录
留痕。若验收要求所有变更必须走变更单，可再补一次变更单流程演练。

## 复现命令

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm test && npm run build

# 真实采集（临时库）
SQLITE_DB_PATH=/tmp/netops_acceptance.db \
  .venv/bin/python -m collector.run_collect --devices HQ-R1,BR-A-R2

# 10 场景对比（临时主库，避免污染 data/ops.db）
SQLITE_DB_PATH=/tmp/netops_eval.db \
  .venv/bin/python -m test_cases.compare_kb \
  --skip-kb-build --output /tmp/compare_results.json
```

---

## 2026-11-09 增量：真实 VLAN 故障试点

本节保留上方 2026-10-27 执行记录与 2026-11-01 更正，不覆盖历史结论。

### 环境与采集

EVE 的 ZeroTier 成员 `89560f78b7` 恢复 `DIRECT` 后，`.218`、6 台设备管理 IP、
EVE HTTP、控制台端口和设备 SSH 全部恢复。全量真实采集：

```text
run_a118540622af49cc9427edee09297c3e
9 台设备；SSH 39/39；终端连通性 3/3；SNMP 3/6（部分设备超时）
```

### VLAN 试点 1（暴露问题，已更正评分）

```text
BR-A-SW2 Ethernet1/0 access VLAN 10 -> 20
故障批次 run_6583d2bfc6dc4397a1e50461b9ace7c4（0/1）
实验 experiment_ce80695d0b9549ea98f051b560acb113
诊断 5 / 6
恢复 run_5f6bb329c6dd41a19e320bf0f8d2fdb5（1/1）
```

旧评分曾判 `correct`，但复核发现证据选择未把 `VLAN 20: Ethernet1/0` 送入上下文，
两条回答实际归因到 ACL/trunk，均未点名故障端口。已修复：

- `rag_engine/evidence_builder.py` 提升 active 且带端口 VLAN 的优先级；
- 评分新增端口断言 `Ethernet1/0` / `Et1/0`；
- 用 `scripts/rescore_experiment.py` 把诊断 5/6 改为 `wrong`，原始分数保存在
  `evaluation_json.rescore_history` 并写入 `audit_log`。

### VLAN 试点 2（真实通过）

```text
BR-A-SW2 Ethernet1/0 access VLAN 10 -> 20（人工确认 + 备份）
注入备份 reports/backups/BR-A-SW2_20261109_152927.cfg
故障批次 run_cc8b71d38688498fb7ecee4cbf5945a8（0/1）
实验 experiment_e28f65065b0242209a32886749a11dee
诊断 7（有 KB）/ 8（无 KB），均 correct
命中 VLAN、20、Ethernet1/0、Et1/0
恢复批次 run_b92a14f6564c458fbfdb83988b9becec（1/1）
恢复备份 reports/backups/BR-A-SW2_20261109_153125.cfg
最终配置 Ethernet1/0 access VLAN 10；最终 ping 100%
报告 reports/diagnosis_7.md、reports/diagnosis_7.html
人工记录 manual_actions.id=4（completed）
```

### 当前审计

```text
PASS：N1–N4；C1–C7；K1–K3；L1–L5；E1；O2–O3；P1–P3
WARN：K4 source_url 0/26；O1 change_request 0
FAIL：E2 评测结果 6/20；E3 测试用例 3/10；E4 真实故障注入 2/8
```

结论：真实 VLAN 故障链路已完整跑通（注入→采集→有/无 KB 诊断→恢复→复测→落库），
但需求 7 的 10 例/20 条真实故障 campaign 仍未完成。

### 2026-11-09 续：真实 4 类故障 campaign 完成

在 VLAN 试点之后，继续完成了链路、默认路由、ACL、正常和证据不足用例。所有操作
均通过审计终端备份 + 一次性确认 + 下发，并在脚本 `finally` 中强制恢复。

| 类别 | 用例 1 | 用例 2 |
|---|---|---|
| 链路 down | `REAL_LINK_01`（BR-A-R2 Et0/0 shutdown） | `REAL_LINK_02`（BR-B-R3 Et0/0 shutdown） |
| VLAN 错配 | `REAL_VLAN_01`（BR-A-SW2 Et1/0 VLAN10->20） | `REAL_VLAN_02`（同左，修复证据后） |
| 默认路由缺失 | `REAL_DEFAULT_ROUTE_01`（BR-A-R2） | `REAL_DEFAULT_ROUTE_02`（BR-B-R3） |
| ACL 过度封锁 | `REAL_ACL_01`（HQ-R1 Et1/0） | `REAL_ACL_02`（HQ-R1 Et2/0） |
| 正常 | `REAL_NORMAL_01`（历史 stale 负向控制） | `REAL_NORMAL_02`（恢复后全量采集） |
| 证据不足 | `REAL_INSUFFICIENT_01`（v1 失败尝试） | `REAL_INSUFFICIENT_01` v2（跳过 BR-B-SW3 的 full 结果） |

有效 campaign（每个用例取最终实验，排除 stale 负向控制和早期失败尝试）：

| 指标 | 有知识库 | 无知识库 | 结论 |
|---|---:|---:|---|
| 准确率 | 85.0%（8C/1P/1W） | 80.0%（7C/2P/1W） | 有 KB ≥ 80% 且 ≥ 无 KB，通过 |
| 平均可执行性 | 0.875 | 1.000 | 有 KB ≥ 0.75，通过 |
| 结果数 | 10 | 10 | 20 次对比，通过 |

本轮发现并修复了三个影响评测有效性的问题：

1. 问题文本同时含 IP 和站点时，设备提取器只返回 IP 对应设备，ACL 故障上下文
   丢失；已合并站点设备并补测试。
2. 全局证据上限 30 条会把 HQ-R1 的故障 ACL 挤出上下文；已改为 36 条并按设备
   公平分配，补充 ACL 证据保留测试。
3. 证据不足 v1 使用 `single` 结果，被最新 `full` 结果回退覆盖；v2 改为发布
   `full` 类型的 skipped 结果。v1 保留为失败尝试，不计入有效指标。

最终只读审计：

```text
PASS：N1–N4、C1–C7、K1–K3、L1–L5
PASS：E1 实验 14、E2 结果 28、E3 用例 11、E4 真实故障注入 10
PASS：O2–O3、P1–P3
WARN：K4 source_url 0/26；O1 正式变更单 0
```

最终全量采集 `run_c39489b83b664cdea85372a8cd3ca8b7`：9 台设备、SSH 39/0、
SNMP 6/6、连通性 3/3。所有故障已恢复：VLAN 10、两条默认路由存在、
链路 up/up、HQ-R1 两个分支接口恢复 `WAN-IN`。

结论：需求 7 的“4 类各 2 例 + 正常 + 证据不足、10 个用例、20 条结果”硬指标
已满足；剩余 WARN 为知识库 `source_url` 和正式变更单，不再是真实故障 campaign
的缺口。
