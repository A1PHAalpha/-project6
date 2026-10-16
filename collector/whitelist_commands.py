"""
只读命令白名单。采集模块和操作控制层共用此列表，
确保任何自动化/模型建议都不能跳出"只读检查"的范围。
"""

# device_type (netmiko 定义) -> 采集项名称 -> 命令
READONLY_COMMANDS = {
    "cisco_ios": {
        "interface_status": "show ip interface brief",
        "vlan_brief": "show vlan brief",
        "route_table": "show ip route",
        "running_config": "show running-config",
        "cdp_neighbors": "show cdp neighbors",
        "access_list": "show access-lists",
        "arp_table": "show arp",
    },
    "huawei_vrp": {
        "interface_status": "display ip interface brief",
        "vlan_brief": "display vlan brief",
        "route_table": "display ip routing-table",
        "running_config": "display current-configuration",
        "access_list": "display acl all",
    },
    "linux": {
        "ip_addr": "ip addr show",
        "route_table": "ip route show",
        "ping_gateway": None,  # 由采集脚本动态拼接目标地址，见 collector_ssh.py
    },
}


def is_readonly_command(device_type: str, command: str) -> bool:
    """供操作控制层调用：判断一条命令是否在只读白名单内。"""
    cmds = READONLY_COMMANDS.get(device_type, {})
    return command in cmds.values()


def get_command(device_type: str, item: str) -> str:
    return READONLY_COMMANDS.get(device_type, {}).get(item)
