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
