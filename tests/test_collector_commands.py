from collector import collector_ssh
from collector.collector_ssh import is_invalid_command_output
from collector.whitelist_commands import get_collection_commands


def test_router_omits_vlan_brief():
    commands = get_collection_commands(
        {
            "device_type": "cisco_ios",
            "model": "IOSv (EVE-NG)",
            "role": "总部核心路由器",
        }
    )
    assert "vlan_brief" not in commands
    assert "interface_status" in commands


def test_switch_keeps_vlan_brief():
    commands = get_collection_commands(
        {
            "device_type": "cisco_ios",
            "model": "IOSvL2 (EVE-NG)",
            "role": "总部核心交换机",
        }
    )
    assert commands["vlan_brief"] == "show vlan brief"


def test_unknown_profile_keeps_full_command_set():
    commands = get_collection_commands({"device_type": "cisco_ios"})
    assert "vlan_brief" in commands


def test_cisco_invalid_input_is_detected():
    assert (
        is_invalid_command_output("% Invalid input detected at '^' marker.")
        is True
    )


def test_cisco_incomplete_and_ambiguous_are_detected():
    assert is_invalid_command_output("% Incomplete command.") is True
    assert is_invalid_command_output('% Ambiguous command: "sh v"') is True


def test_normal_output_is_not_invalid():
    assert (
        is_invalid_command_output(
            "Interface  IP-Address  Status\nEt0/0  10.0.0.1  up"
        )
        is False
    )


def test_collect_device_marks_invalid_output_failed(monkeypatch):
    class FakeConnection:
        def send_command(self, command, read_timeout=15):
            if command == "show vlan brief":
                return "% Invalid input detected at '^' marker."
            return "ok"

        def disconnect(self):
            pass

    monkeypatch.setattr(
        collector_ssh, "ConnectHandler", lambda **_kwargs: FakeConnection()
    )
    device = {
        "device_name": "HQ-SW1",
        "device_type": "cisco_ios",
        "model": "IOSvL2 (EVE-NG)",
        "role": "总部核心交换机",
        "mgmt_ip": "192.0.2.1",
    }

    results = collector_ssh.collect_device(device, "admin", "password")

    vlan_result = next(result for result in results if result["item"] == "vlan_brief")
    assert vlan_result["status"] == "failed"
    assert vlan_result["error_code"] == "invalid_command_output"
    assert "Invalid input" in vlan_result["raw_output"]
