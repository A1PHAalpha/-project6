"""只读命令白名单单测：变更类命令必须一律被拒绝。"""
from collector.whitelist_commands import get_command, is_readonly_command


def test_readonly_command_allowed():
    assert is_readonly_command("cisco_ios", "show ip interface brief") is True
    assert is_readonly_command("huawei_vrp", "display vlan brief") is True


def test_change_commands_rejected():
    for cmd in ["configure terminal", "shutdown", "no shutdown", "write memory", "reload", "delete flash:"]:
        assert is_readonly_command("cisco_ios", cmd) is False


def test_unknown_device_type_rejected():
    assert is_readonly_command("juniper_junos", "show version") is False


def test_get_command_lookup():
    assert get_command("cisco_ios", "route_table") == "show ip route"
    assert get_command("cisco_ios", "not_exist") is None
