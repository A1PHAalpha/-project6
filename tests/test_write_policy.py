from control.write_policy import is_write_command


def test_common_write_commands_are_detected():
    for command in (
        "configure terminal",
        "interface GigabitEthernet0/1",
        "no shutdown",
        "ip route 0.0.0.0 0.0.0.0 10.0.0.1",
        "write memory",
        "undo shutdown",
        "system-view",
    ):
        assert is_write_command("cisco_ios", command) is True


def test_read_only_commands_are_not_write():
    for command in ("show ip interface brief", "display version", "ping 10.0.0.1"):
        assert is_write_command("cisco_ios", command) is False


def test_abbreviations_negations_and_platform_commands_are_write():
    for command in (
        "no ip route 0.0.0.0 0.0.0.0 10.0.0.1",
        "no shutdown",
        "no shut",
        "shut",
        "ip address 10.0.0.2 255.255.255.0",
        "hostname X",
        "username admin secret x",
        "line vty 0 4",
        "wr mem",
        "save",
        "sudo systemctl restart nginx",
        "iptables -F",
        "ip link set eth0 down",
    ):
        assert is_write_command("cisco_ios", command) is True, command


def test_multi_line_or_compound_commands_are_write():
    for command in ("show version\nconfigure terminal", "show version; reload"):
        assert is_write_command("cisco_ios", command) is True
