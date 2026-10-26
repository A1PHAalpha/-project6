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


def _device_text(device: dict) -> str:
    return f"{device.get('model') or ''} {device.get('role') or ''}".lower()


def _looks_like_switch(device: dict) -> bool:
    text = _device_text(device)
    return any(marker in text for marker in ("iosvl2", "switch", "交换机"))


def _looks_like_router(device: dict) -> bool:
    text = _device_text(device)
    return any(marker in text for marker in ("iosv", "router", "路由器"))


def get_collection_commands(device: dict) -> dict[str, str]:
    """Return the read-only command set for a device role.

    Cisco IOS routers do not implement the switch-only ``show vlan brief``
    command. Unknown profiles keep the full command set to avoid silently
    removing capabilities.
    """
    device_type = device.get("device_type") or ""
    commands = dict(READONLY_COMMANDS.get(device_type, {}))
    if (
        device_type == "cisco_ios"
        and _looks_like_router(device)
        and not _looks_like_switch(device)
    ):
        commands.pop("vlan_brief", None)
    return commands
