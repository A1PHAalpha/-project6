"""
效果测试场景定义（任务9）。

每个场景描述:
- id: 场景唯一标识
- category: link_down / vlan_misconfig / default_route_missing / acl_overblock / normal / insufficient_evidence
- question: 向诊断引擎提出的自然语言问题
- seed_records: 需要预先写入 collection_records 的模拟采集数据
    每条为 dict(device_name, status, command, raw_output=None, parsed=None, fail_reason=None)
- expected_notes: 人工评估时的参考要点（不是自动判分标准，仅供对照用）

本模块不依赖 GNS3 拓扑是否就位：所有证据通过 seed_records 直接写入
collector.db 的 collection_records 表，模拟"已采集到的真实状态"，
与 collector_ssh.py/collector_snmp.py 实际采集后落盘的数据结构完全一致，
因此一旦接入真实采集，只需不再调用 seed，diagnose() 的调用方式不变。
"""

# 4类故障 x 2案例 + 1正常 + 1证据不足 = 10个场景
SCENARIOS = [
    # ---------- 1. 链路 down (2例) ----------
    {
        "id": "LINK_DOWN_01",
        "category": "link_down",
        "question": "分支A路由器和交换机之间好像不通了，能帮忙看看是什么问题吗？",
        "seed_records": [
            {
                "device_name": "BR-A-R2",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    "GigabitEthernet0/0     192.168.100.2   YES NVRAM  up                    up\n"
                    "GigabitEthernet0/1     192.168.20.1    YES NVRAM  down                  down\n"
                ),
                "parsed": {"item": "interface_status", "up_count": 1, "down_count": 1},
            },
            {
                "device_name": "BR-A-SW2",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    "GigabitEthernet0/0     unassigned      YES NVRAM  down                  down\n"
                    "GigabitEthernet0/1     unassigned      YES NVRAM  up                    up\n"
                ),
                "parsed": {"item": "interface_status", "up_count": 1, "down_count": 1},
            },
        ],
        "expected_notes": "应识别出 BR-A-R2 的 Gi0/1 与 BR-A-SW2 的 Gi0/0 均为 down/down，判断为分支A路由器-交换机互联链路故障，建议检查物理链路/线缆/端口是否误关闭。",
    },
    {
        "id": "LINK_DOWN_02",
        "category": "link_down",
        "question": "总部核心交换机连分支B的那个口状态正常吗？分支B现在反馈访问总部很慢甚至不通。",
        "seed_records": [
            {
                "device_name": "HQ-SW1",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    "GigabitEthernet0/0     192.168.100.11  YES NVRAM  up                    up\n"
                    "GigabitEthernet0/1     192.168.100.11  YES NVRAM  up                    up\n"
                    "GigabitEthernet0/2     192.168.100.11  YES NVRAM  down                  down\n"
                ),
                "parsed": {"item": "interface_status", "up_count": 2, "down_count": 1},
            },
            {
                "device_name": "BR-B-PC",
                "status": "failed",
                "command": "ping 192.168.10.100",
                "fail_reason": "目标 192.168.10.100 不可达",
            },
        ],
        "expected_notes": "HQ-SW1 的 Gi0/2（推测为连接分支B方向）down/down，且 BR-B-PC 到总部服务器 ping 失败，两条证据应被关联到同一故障判断。",
    },

    # ---------- 2. VLAN 配置错 (2例) ----------
    {
        "id": "VLAN_MISCONFIG_01",
        "category": "vlan_misconfig",
        "question": "分支A的测试终端连不上同VLAN的其他设备，怀疑是VLAN配置问题。",
        "seed_records": [
            {
                "device_name": "BR-A-SW2",
                "status": "success",
                "command": "show vlan brief",
                "raw_output": (
                    "VLAN Name                             Status    Ports\n"
                    "1    default                          active    \n"
                    "10   USERS                            active    Gi0/1\n"
                    "20   SERVERS                          active    Gi0/0\n"
                ),
                "parsed": {"item": "vlan_brief", "vlan_lines": ["10   USERS", "20   SERVERS"]},
            },
        ],
        "expected_notes": "台账记录 BR-A-SW2 只应有 VLAN 10，但采集到端口 Gi0/0 被划入了 VLAN 20（SERVERS），与台账预期不符，应判断为端口VLAN分配错误。",
    },
    {
        "id": "VLAN_MISCONFIG_02",
        "category": "vlan_misconfig",
        "question": "总部交换机和分支A交换机之间的trunk是不是没放通VLAN10？两边终端互相看不到。",
        "seed_records": [
            {
                "device_name": "HQ-SW1",
                "status": "success",
                "command": "show vlan brief",
                "raw_output": (
                    "VLAN Name                             Status    Ports\n"
                    "1    default                          active    \n"
                    "10   USERS                            active    \n"
                    "20   SERVERS                          active    Gi0/2\n"
                ),
                "parsed": {"item": "vlan_brief", "vlan_lines": ["10   USERS", "20   SERVERS"]},
            },
            {
                "device_name": "HQ-SW1",
                "status": "success",
                "command": "show running-config",
                "raw_output": (
                    "interface GigabitEthernet0/1\n"
                    " switchport trunk encapsulation dot1q\n"
                    " switchport mode trunk\n"
                    " switchport trunk allowed vlan 20\n"
                ),
                "parsed": {"item": "running_config", "line_count": 4},
            },
        ],
        "expected_notes": "trunk 口 allowed vlan 只放通了20，未包含10，应判断为trunk未放通所需VLAN，建议补充 allowed vlan 配置（需人工确认后操作）。",
    },

    # ---------- 3. 缺省路由丢失 (2例) ----------
    {
        "id": "DEFAULT_ROUTE_MISSING_01",
        "category": "default_route_missing",
        "question": "分支B的终端访问总部服务器一直超时，路由是不是有问题？",
        "seed_records": [
            {
                "device_name": "BR-B-R3",
                "status": "success",
                "command": "show ip route",
                "raw_output": (
                    "Gateway of last resort is not set\n\n"
                    "     192.168.30.0/24 is directly connected, GigabitEthernet0/1\n"
                    "     192.168.100.0/24 is directly connected, GigabitEthernet0/0\n"
                ),
                "parsed": {"item": "route_table", "has_default_route": False},
            },
            {
                "device_name": "BR-B-PC",
                "status": "failed",
                "command": "ping 192.168.10.100",
                "fail_reason": "目标 192.168.10.100 不可达",
            },
        ],
        "expected_notes": "BR-B-R3 路由表中 Gateway of last resort is not set，且无 0.0.0.0/0 条目，应判断为缺省路由丢失，建议人工确认后补充静态缺省路由或检查动态路由协议状态。",
    },
    {
        "id": "DEFAULT_ROUTE_MISSING_02",
        "category": "default_route_missing",
        "question": "总部到两个分支的互访都正常，但是分支A访问不了外网/其他未知网段，什么情况？",
        "seed_records": [
            {
                "device_name": "BR-A-R2",
                "status": "success",
                "command": "show ip route",
                "raw_output": (
                    "Gateway of last resort is not set\n\n"
                    "     192.168.20.0/24 is directly connected, GigabitEthernet0/1\n"
                    "     192.168.100.0/24 is directly connected, GigabitEthernet0/0\n"
                    "O    192.168.30.0/24 [110/2] via 192.168.100.1, 00:12:33\n"
                ),
                "parsed": {"item": "route_table", "has_default_route": False},
            },
        ],
        "expected_notes": "BR-A-R2 有到分支B的OSPF路由，但没有缺省路由条目，访问未知网段会失败，应判断为缺省路由缺失（而非OSPF邻居故障，证据中OSPF路由正常）。",
    },

    # ---------- 4. ACL 过度封锁 (2例) ----------
    {
        "id": "ACL_OVERBLOCK_01",
        "category": "acl_overblock",
        "question": "分支A终端突然访问不了总部服务器了，之前一直是通的，怀疑是不是ACL改动导致的。",
        "seed_records": [
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show access-lists",
                "raw_output": (
                    "Extended IP access list BLOCK_BRANCH\n"
                    "    10 deny ip 192.168.20.0 0.0.0.255 any\n"
                    "    20 permit ip any any\n"
                ),
                "parsed": {"item": "access_list", "acl_present": True},
            },
            {
                "device_name": "BR-A-PC",
                "status": "failed",
                "command": "ping 192.168.10.100",
                "fail_reason": "目标 192.168.10.100 不可达",
            },
        ],
        "expected_notes": "HQ-R1 的 ACL BLOCK_BRANCH 第10条规则 deny 了分支A全网段(192.168.20.0/24)，优先于后面的 permit any any 生效，应判断为ACL规则过度封锁导致分支A无法访问总部，建议人工确认后收窄或移除该deny规则。",
    },
    {
        "id": "ACL_OVERBLOCK_02",
        "category": "acl_overblock",
        "question": "分支B能ping通总部网关，但是访问总部服务器的具体业务端口不通，是不是ACL只放通了部分流量？",
        "seed_records": [
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show access-lists",
                "raw_output": (
                    "Extended IP access list LIMIT_BRANCH_B\n"
                    "    10 permit icmp 192.168.30.0 0.0.0.255 any\n"
                    "    20 deny ip 192.168.30.0 0.0.0.255 any\n"
                    "    30 permit ip any any\n"
                ),
                "parsed": {"item": "access_list", "acl_present": True},
            },
        ],
        "expected_notes": "ACL LIMIT_BRANCH_B 只放通了ICMP(第10条)，第20条deny了分支B全部其他IP流量，应判断为ACL只允许了ping，业务流量被过度封锁，建议人工确认后补充所需业务端口的permit规则。",
    },

    # ---------- 5. 正常案例 ----------
    {
        "id": "NORMAL_01",
        "category": "normal",
        "question": "帮忙检查一下总部和分支A之间的网络现在是否正常。",
        "seed_records": [
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    "GigabitEthernet0/0     192.168.100.1   YES NVRAM  up                    up\n"
                    "GigabitEthernet0/1     192.168.200.1   YES NVRAM  up                    up\n"
                ),
                "parsed": {"item": "interface_status", "up_count": 2, "down_count": 0},
            },
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show ip route",
                "raw_output": (
                    "Gateway of last resort is not set\n\n"
                    "S    192.168.10.0/24 [1/0] via 192.168.100.2\n"
                    "     192.168.200.0/24 is directly connected, GigabitEthernet0/1\n"
                ),
                "parsed": {"item": "route_table", "has_default_route": False},
            },
            {
                "device_name": "BR-A-R2",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    "GigabitEthernet0/0     192.168.100.2   YES NVRAM  up                    up\n"
                    "GigabitEthernet0/1     192.168.10.1    YES NVRAM  up                    up\n"
                ),
                "parsed": {"item": "interface_status", "up_count": 2, "down_count": 0},
            },
            {
                "device_name": "BR-A-R2",
                "status": "success",
                "command": "show ip route",
                "raw_output": (
                    "Gateway of last resort is 192.168.100.1 to network 0.0.0.0\n\n"
                    "S*   0.0.0.0/0 [1/0] via 192.168.100.1\n"
                    "S    192.168.200.0/24 [1/0] via 192.168.100.1\n"
                    "     192.168.10.0/24 is directly connected, GigabitEthernet0/1\n"
                ),
                "parsed": {"item": "route_table", "has_default_route": True},
            },
            {
                "device_name": "BR-A-PC",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    "GigabitEthernet0/0     192.168.10.10   YES NVRAM  up                    up\n"
                ),
                "parsed": {"item": "interface_status", "up_count": 1, "down_count": 0},
            },
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "ping 192.168.10.10",
                "raw_output": "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/1/1 ms",
                "parsed": {"target_ip": "192.168.10.10", "reachable": True, "success_rate": 100},
            },
            {
                "device_name": "BR-A-R2",
                "status": "success",
                "command": "ping 192.168.200.10",
                "raw_output": "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/1/1 ms",
                "parsed": {"target_ip": "192.168.200.10", "reachable": True, "success_rate": 100},
            },
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show access-lists",
                "raw_output": "",
                "parsed": {"item": "access_list", "acl_present": False},
            },
            {
                "device_name": "BR-A-R2",
                "status": "success",
                "command": "show access-lists",
                "raw_output": "",
                "parsed": {"item": "access_list", "acl_present": False},
            },
            {
                "device_name": "HQ-SRV",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    "Ethernet0              192.168.200.10  YES NVRAM  up                    up\n"
                ),
                "parsed": {"item": "interface_status", "up_count": 1, "down_count": 0},
            },
            {
                "device_name": "BR-A-SW2",
                "status": "success",
                "command": "show vlan brief",
                "raw_output": (
                    "VLAN Name                             Status    Ports\n"
                    "1    default                          active    Gi0/2\n"
                    "10   USERS                            active    Gi0/1\n"
                ),
                "parsed": {"item": "vlan_brief", "vlan_lines": ["10   USERS"]},
            },
            {
                "device_name": "HQ-SW1",
                "status": "success",
                "command": "show vlan brief",
                "raw_output": (
                    "VLAN Name                             Status    Ports\n"
                    "1    default                          active    Gi0/2\n"
                    "20   SERVERS                          active    Gi0/1\n"
                ),
                "parsed": {"item": "vlan_brief", "vlan_lines": ["20   SERVERS"]},
            },
            {
                "device_name": "BR-A-PC",
                "status": "success",
                "command": "ping 192.168.200.10",
                "raw_output": "4 packets transmitted, 4 received, 0% packet loss",
                "parsed": {"purpose": "分支A到总部服务器连通性测试", "target_ip": "192.168.200.10", "reachable": True},
            },
        ],
        "expected_notes": "所有证据均显示正常（接口up、静态路由存在、VLAN正常、ping成功），模型应判断当前无故障，不应为了'配合问题'而编造异常。",
    },

    # ---------- 6. 证据不足案例 ----------
    {
        "id": "INSUFFICIENT_EVIDENCE_01",
        "category": "insufficient_evidence",
        "question": "分支B交换机是不是有端口被误关闭了？帮我分析一下原因并给出修复步骤。",
        "seed_records": [
            # 故意不写入 BR-B-SW3 的任何采集记录，模拟"尚未采集/采集失败且无历史数据"的情况
        ],
        "expected_notes": "BR-B-SW3 无任何采集记录，evidence_builder 会返回 status='no_data' 的占位证据。"
                          "模型应在 missing_information 中明确说明缺少该设备的接口状态证据，"
                          "不能编造具体是哪个端口被关闭或凭空给出修复步骤。",
    },
]


def seed_scenario(scenario: dict):
    """
    将指定场景的 seed_records 写入 collection_records 表。
    依赖调用方已先执行 collector.db.init_db() 并加载设备台账。
    """
    from collector import db

    records = [
        {
            "device_name": rec["device_name"],
            "status": rec["status"],
            "command": rec.get("command"),
            "raw_output": rec.get("raw_output"),
            "parsed": rec.get("parsed"),
            "fail_reason": rec.get("fail_reason"),
        }
        for rec in scenario["seed_records"]
    ]
    db.insert_collection_batch(
        records,
        trigger_type="synthetic",
        result_type="full",
        requested_by="test_scenario",
    )


def get_scenario(scenario_id: str) -> dict:
    for s in SCENARIOS:
        if s["id"] == scenario_id:
            return s
    raise KeyError(f"未找到场景: {scenario_id}")
