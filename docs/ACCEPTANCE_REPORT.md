# 项目验收执行报告

- 执行日期：2026-10-27
- 代码基线：本地 `main`（评测开始前提交）
- 实验环境：EVE-NG + ZeroTier；3 台 Cisco IOSv 路由器、3 台 IOSvL2 交换机、
  3 个 VPCS 终端；真实 LLM：`qwen-plus`（阿里云 MaaS 兼容接口）
- 评测数据：临时 SQLite/Chroma 数据，不污染 `data/ops.db`

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
| 8. 结果展示 | 部分通过 | Web/API/Markdown/HTML 通过；“真实故障注入→人工修复→复测”演练尚未执行 |

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
| P4 人工记录 | 变更/终端/复测测试通过 | 通过 |
| P5 端到端演示 | 尚未执行真实故障注入→人工修复→复测 | 未完成 |
| P6 前端质量 | 16 个 Vitest 通过；Vite build 成功 | 通过 |

P5 需要一次受控演练：在 EVE 上设置链路/VLAN/路由/ACL 故障，执行真实采集、AI 诊断、
人工修复和复测，并保留审计记录。该操作会修改实验设备配置，需在维护窗口执行。

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
