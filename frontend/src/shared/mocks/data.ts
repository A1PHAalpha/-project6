// Deterministic examples keep the console usable without a live backend.
import type {
  CollectionRun,
  Device,
  DiagnosisSession,
  KnowledgeArticle,
  KnowledgeSummary,
  OverviewData,
  Topology,
  TopologyEdge,
  TopologyNode
} from "../types";

const observedAt = "2026-10-20T03:18:00+08:00";
const runId = "run_534d4bc9a";

export const mockDevices: Device[] = [
  {
    id: "dev_br_a_pc",
    name: "BR-A-PC",
    role: "分支A测试终端",
    site: "分支A",
    vendor: "N/A",
    model: "VPCS/Linux",
    platform: "VPCS / Linux",
    managementIp: "192.168.20.100",
    reachability: "unreachable",
    overallStatus: "failed",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f1", type: "reachability", label: "到 HQ-SRV", value: "ping 100% loss", observedAt, tone: "danger" },
      { id: "f2", type: "system", label: "管理通道", value: "SSH 不可用", observedAt, tone: "warning" }
    ],
    interfaces: ["eth0"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_br_a_r2",
    name: "BR-A-R2",
    role: "分支A路由器",
    site: "分支A",
    vendor: "Cisco",
    model: "IOSv",
    platform: "Cisco IOSv",
    managementIp: "192.168.249.12",
    reachability: "reachable",
    overallStatus: "partial",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f3", type: "interface", label: "Gi0/1", value: "down / down", observedAt, tone: "warning" },
      { id: "f4", type: "route", label: "路由表", value: "18 条路由", observedAt, tone: "success" }
    ],
    interfaces: ["Gi0/0", "Gi0/1"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_br_a_sw2",
    name: "BR-A-SW2",
    role: "分支A交换机",
    site: "分支A",
    vendor: "Cisco",
    model: "IOSvL2",
    platform: "Cisco IOSvL2",
    managementIp: "192.168.249.15",
    reachability: "reachable",
    overallStatus: "success",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f5", type: "interface", label: "Gi0/0", value: "down / down", observedAt, tone: "warning" },
      { id: "f6", type: "vlan", label: "VLAN 10", value: "active · 2 个接口", observedAt, tone: "success" }
    ],
    interfaces: ["Gi0/0", "Gi0/1"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_br_b_pc",
    name: "BR-B-PC",
    role: "分支B测试终端",
    site: "分支B",
    vendor: "N/A",
    model: "VPCS/Linux",
    platform: "VPCS / Linux",
    managementIp: "192.168.30.100",
    reachability: "unreachable",
    overallStatus: "failed",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f7", type: "reachability", label: "到 HQ-SRV", value: "ping timeout", observedAt, tone: "danger" }
    ],
    interfaces: ["eth0"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_br_b_r3",
    name: "BR-B-R3",
    role: "分支B路由器",
    site: "分支B",
    vendor: "Cisco",
    model: "IOSv",
    platform: "Cisco IOSv",
    managementIp: "192.168.249.13",
    reachability: "reachable",
    overallStatus: "success",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f8", type: "interface", label: "Gi0/2", value: "up / up", observedAt, tone: "success" },
      { id: "f9", type: "route", label: "路由表", value: "18 条路由", observedAt, tone: "success" }
    ],
    interfaces: ["Gi0/0", "Gi0/1", "Gi0/2"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_br_b_sw3",
    name: "BR-B-SW3",
    role: "分支B交换机",
    site: "分支B",
    vendor: "Cisco",
    model: "IOSvL2",
    platform: "Cisco IOSvL2",
    managementIp: "192.168.249.16",
    reachability: "reachable",
    overallStatus: "success",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f10", type: "interface", label: "Gi0/1", value: "up / up", observedAt, tone: "success" },
      { id: "f11", type: "vlan", label: "VLAN 10", value: "active · 2 个接口", observedAt, tone: "success" }
    ],
    interfaces: ["Gi0/0", "Gi0/1"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_hq_r1",
    name: "HQ-R1",
    role: "总部核心路由器",
    site: "总部",
    vendor: "Cisco",
    model: "IOSv",
    platform: "Cisco IOSv",
    managementIp: "192.168.249.11",
    reachability: "reachable",
    overallStatus: "success",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f12", type: "interface", label: "Gi0/1", value: "up / up", observedAt, tone: "success" },
      { id: "f13", type: "route", label: "OSPF", value: "邻居 2 · 收敛正常", observedAt, tone: "success" }
    ],
    interfaces: ["Gi0/0", "Gi0/1", "Gi0/2"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_hq_srv",
    name: "HQ-SRV",
    role: "总部服务器终端",
    site: "总部",
    vendor: "N/A",
    model: "VPCS/Linux",
    platform: "VPCS / Linux",
    managementIp: "192.168.10.100",
    reachability: "unknown",
    overallStatus: "skipped",
    collectedAt: null,
    runId: null,
    facts: [
      { id: "f14", type: "system", label: "采集状态", value: "未采集 · 无管理通道", observedAt, tone: "neutral" }
    ],
    interfaces: ["eth0"],
    lifecycleStatus: "active"
  },
  {
    id: "dev_hq_sw1",
    name: "HQ-SW1",
    role: "总部核心交换机",
    site: "总部",
    vendor: "Cisco",
    model: "IOSvL2",
    platform: "Cisco IOSvL2",
    managementIp: "192.168.249.14",
    reachability: "reachable",
    overallStatus: "success",
    collectedAt: observedAt,
    runId,
    facts: [
      { id: "f15", type: "vlan", label: "VLAN 10 / 20", value: "active", observedAt, tone: "success" },
      { id: "f16", type: "neighbor", label: "CDP 邻接", value: "3 个邻居", observedAt, tone: "success" }
    ],
    interfaces: ["Gi0/0", "Gi0/1", "Gi0/2"],
    lifecycleStatus: "active"
  }
];

export const mockRuns: CollectionRun[] = [
  {
    id: runId,
    trigger: "startup",
    scope: "全网 · 9 台",
    status: "partial",
    deviceSummary: "6 成功 · 2 失败 · 1 跳过",
    sshSummary: "40 / 42 成功",
    connectivitySummary: "2 条超时",
    startedAt: "2026-10-20T03:17:19+08:00",
    publishedAt: observedAt,
    durationSeconds: 41
  },
  {
    id: "run_cbaab208e",
    trigger: "manual",
    scope: "HQ-R1",
    status: "success",
    deviceSummary: "1 成功",
    sshSummary: "7 / 7 成功",
    connectivitySummary: "正常",
    startedAt: "2026-10-20T02:36:25+08:00",
    publishedAt: "2026-10-20T02:37:00+08:00",
    durationSeconds: 35
  },
  {
    id: "run_99129140a",
    trigger: "manual",
    scope: "HQ-R1",
    status: "failed",
    deviceSummary: "SSH 连接失败",
    sshSummary: "0 / 1 成功",
    connectivitySummary: "未执行",
    startedAt: "2026-10-20T02:36:49+08:00",
    publishedAt: null,
    durationSeconds: 11
  }
];

const nodes: TopologyNode[] = [
  { id: "HQ-R1", deviceName: "HQ-R1", role: "总部核心路由器", site: "总部", managementIp: "192.168.249.11", reachability: "reachable", summary: "OSPF 收敛正常", x: 195, y: 105 },
  { id: "HQ-SW1", deviceName: "HQ-SW1", role: "总部核心交换机", site: "总部", managementIp: "192.168.249.14", reachability: "reachable", summary: "VLAN 10 / 20", x: 195, y: 275 },
  { id: "HQ-SRV", deviceName: "HQ-SRV", role: "总部服务器终端", site: "总部", managementIp: "192.168.10.100", reachability: "unknown", summary: "未采集", x: 410, y: 200 },
  { id: "BR-A-R2", deviceName: "BR-A-R2", role: "分支A路由器", site: "分支A", managementIp: "192.168.249.12", reachability: "reachable", summary: "Gi0/1 DOWN", x: 730, y: 105 },
  { id: "BR-A-SW2", deviceName: "BR-A-SW2", role: "分支A交换机", site: "分支A", managementIp: "192.168.249.15", reachability: "reachable", summary: "Gi0/0 DOWN", x: 730, y: 275 },
  { id: "BR-A-PC", deviceName: "BR-A-PC", role: "分支A测试终端", site: "分支A", managementIp: "192.168.20.100", reachability: "unreachable", summary: "ping 超时", x: 945, y: 200 },
  { id: "BR-B-R3", deviceName: "BR-B-R3", role: "分支B路由器", site: "分支B", managementIp: "192.168.249.13", reachability: "reachable", summary: "路由 18", x: 1255, y: 105 },
  { id: "BR-B-SW3", deviceName: "BR-B-SW3", role: "分支B交换机", site: "分支B", managementIp: "192.168.249.16", reachability: "reachable", summary: "VLAN 10 UP", x: 1255, y: 275 },
  { id: "BR-B-PC", deviceName: "BR-B-PC", role: "分支B测试终端", site: "分支B", managementIp: "192.168.30.100", reachability: "unreachable", summary: "ping 超时", x: 1470, y: 200 }
];

const edges: TopologyEdge[] = [
  { id: "e1", source: "HQ-R1", target: "HQ-SW1", label: "Gi0/0 ↔ Gi0/1", status: "up" },
  { id: "e2", source: "HQ-SW1", target: "HQ-SRV", label: "VLAN 10", status: "up" },
  { id: "e3", source: "HQ-R1", target: "BR-A-R2", label: "WAN", status: "up" },
  { id: "e4", source: "BR-A-R2", target: "BR-A-SW2", label: "Gi0/1 ↔ Gi0/0", status: "down" },
  { id: "e5", source: "BR-A-SW2", target: "BR-A-PC", label: "VLAN 10", status: "warning" },
  { id: "e6", source: "HQ-R1", target: "BR-B-R3", label: "WAN", status: "up" },
  { id: "e7", source: "BR-B-R3", target: "BR-B-SW3", label: "Gi0/2", status: "up" },
  { id: "e8", source: "BR-B-SW3", target: "BR-B-PC", label: "VLAN 10", status: "warning" }
];

export const mockTopology: Topology = {
  runId,
  publishedAt: observedAt,
  nodes,
  edges
};

export const mockKnowledge: KnowledgeSummary = {
  status: "ready",
  indexVersionId: "kbindex_f7a5c2d1",
  articleCount: 26,
  sourceCount: 6,
  chunkCount: 26,
  pendingCount: 2,
  modelName: "bge-small-zh-v1.5",
  modelDimensions: 512,
  checkedAt: observedAt
};

export const mockSession: DiagnosisSession = {
  id: "session_demo_branch_a",
  title: "分支A PC 无法访问总部服务器",
  question: "分支A的PC无法访问总部服务器，请诊断原因。",
  status: "active",
  modelId: "qwen-plus",
  createdAt: "2026-10-20T04:08:00+08:00",
  updatedAt: "2026-10-20T04:12:00+08:00",
  evidence: [
    {
      id: "ev_1",
      deviceName: "BR-A-R2",
      label: "Gi0/1",
      value: "物理状态 UP，协议状态 DOWN",
      collectedAt: observedAt,
      runId,
      tone: "warning"
    },
    {
      id: "ev_2",
      deviceName: "BR-A-SW2",
      label: "Gi0/0",
      value: "物理状态 DOWN，协议状态 DOWN",
      collectedAt: observedAt,
      runId,
      tone: "danger"
    },
    {
      id: "ev_3",
      deviceName: "BR-A-PC",
      label: "到 HQ-SRV",
      value: "ping 100% loss",
      collectedAt: observedAt,
      runId,
      tone: "danger"
    }
  ],
  knowledge: [
    { id: "KB-IF-001", title: "接口 administratively down 手动恢复", version: "v1.0", source: "Cisco 官方文档", distance: 0.42 },
    { id: "KB-IF-002", title: "接口 line protocol down 排查", version: "v1.0", source: "Cisco 官方文档", distance: 0.51 }
  ],
  toolCalls: [
    { id: "tc1", name: "collect_status", deviceName: "BR-A-R2", target: "BR-A-R2", status: "success" },
    { id: "tc2", name: "collect_status", deviceName: "BR-A-SW2", target: "BR-A-SW2", status: "success" },
    { id: "tc3", name: "ping_test", deviceName: "BR-A-PC", target: "BR-A-PC → HQ-SRV", status: "failed" }
  ],
  conclusion: "BR-A-R2 Gi0/1 与 BR-A-SW2 Gi0/0 同时 down，BR-A-PC 到 HQ-SRV 丢包 100%。证据指向分支A路由器与交换机之间的互联链路。",
  causes: [
    { title: "BR-A-R2 与 BR-A-SW2 互联链路异常", basis: "两端接口同时 down，时间点一致", tone: "danger" },
    { title: "分支A终端到服务器路径中断", basis: "ping 100% loss，且上联接口异常", tone: "warning" }
  ],
  suggestion: {
    title: "检查 BR-A-R2 Gi0/1 / BR-A-SW2 Gi0/0 链路",
    actions: ["检查 BR-A-R2 Gi0/1 与 BR-A-SW2 Gi0/0 链路"],
    command: [
      "show interface GigabitEthernet0/1",
      "show interface GigabitEthernet0/0",
      "show cdp neighbors"
    ],
    targetDevice: "BR-A-R2",
    canExecute: true,
    note: "先核对物理链路和接口状态；当前建议只包含人工确认后才可下发的检查命令。",
    requiresManualConfirmation: true
  },
  missingInformation: "仅引用 10-20 03:18 已发布批次，没有历史成功状态可作为当前事实。",
  messages: [
    { id: "m1", role: "user", createdAt: "2026-10-20T04:08:00+08:00", content: "分支A的PC无法访问总部服务器，请诊断原因。" },
    { id: "m2", role: "assistant", createdAt: "2026-10-20T04:12:00+08:00", content: "已完成证据构建和知识检索。诊断结论显示分支A互联链路异常，详见证据卡。" }
  ]
};

export const mockArticles: KnowledgeArticle[] = [
  {
    id: "KB-IF-001",
    title: "接口 administratively down 手动恢复",
    category: "接口",
    applicableDevice: "Cisco IOS",
    version: "v1.0",
    source: "Cisco 官方文档",
    contentHash: "e946a189b2f7",
    hitCount: 3,
    lastHitAt: "2026-10-20T04:12:00+08:00",
    summary: "接口 physical state 为 administratively down。",
    body: "现象：接口 physical state 为 administratively down。\n\n核对：先确认接口描述、对端状态与变更历史。\n\n处理：人工确认影响范围后执行 no shutdown，再检查协议状态。",
    status: "active"
  },
  {
    id: "KB-IF-002",
    title: "接口 line protocol down（physical up）",
    category: "接口",
    applicableDevice: "Cisco IOS",
    version: "v1.0",
    source: "Cisco 官方文档",
    contentHash: "f8139c651d48",
    hitCount: 2,
    lastHitAt: "2026-10-20T03:51:00+08:00",
    summary: "物理层 up，但协议状态 down。",
    body: "现象：接口 physical state 为 up，protocol state 为 down。\n\n核对：检查对端接口、VLAN、封装和时钟配置。\n\n处理：恢复对端或修正二层参数后复测。",
    status: "active"
  },
  {
    id: "KB-VLAN-001",
    title: "接入端口未加入正确 VLAN 导致二层不通",
    category: "VLAN",
    applicableDevice: "Cisco IOS",
    version: "v1.0",
    source: "内部运维手册",
    contentHash: "92d0c1e3d3a7",
    hitCount: 5,
    lastHitAt: "2026-10-19T22:18:00+08:00",
    summary: "接入端口 VLAN 与终端地址规划不一致。",
    body: "检查端口 VLAN、trunk allowed list 和终端网关。修复后通过 ping 与 ARP 表复测。",
    status: "active"
  },
  {
    id: "KB-ROUTE-001",
    title: "默认路由下一跳不可达的定位方法",
    category: "路由",
    applicableDevice: "Cisco IOS",
    version: "v1.0",
    source: "内部运维手册",
    contentHash: "31b71ef1c58a",
    hitCount: 1,
    lastHitAt: "2026-10-19T18:02:00+08:00",
    summary: "默认路由存在但下一跳无法解析。",
    body: "结合 route table、ARP 和接口状态定位下一跳。",
    status: "active"
  },
  {
    id: "KB-ACL-002",
    title: "ACL 拒绝计数快速定位",
    category: "ACL",
    applicableDevice: "Cisco IOS",
    version: "v1.0",
    source: "Cisco 官方文档",
    contentHash: "6a0b8bd92e34",
    hitCount: 0,
    lastHitAt: null,
    summary: "通过 ACL 命中计数定位被拒绝流量。",
    body: "比对 flow 与 ACL 命中顺序，避免修改后扩大放行范围。",
    status: "active"
  }
];

export const mockOverview: OverviewData = {
  devices: { total: 9, reachable: 6, unreachable: 2, unknown: 1 },
  latestRun: mockRuns[0],
  knowledge: mockKnowledge,
  attention: [
    { id: "a1", deviceName: "BR-A-PC", title: "分支A 连通性失败", detail: "ping HQ-SRV 超时 10s，与链路 down 一致", tone: "danger" },
    { id: "a2", deviceName: "BR-B-PC", title: "分支B 连通性失败", detail: "ping HQ-SRV 超时 10s，建议从分支B诊断", tone: "danger" },
    { id: "a3", deviceName: "HQ-SRV", title: "HQ-SRV 未采集", detail: "无管理通道，不能推断为设备故障", tone: "neutral" }
  ],
  recentSessions: [mockSession],
  recentRuns: mockRuns,
  topology: mockTopology
};
