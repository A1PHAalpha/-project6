from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from collector import collector_ssh
from collector.collector_ssh import is_invalid_command_output
from collector.whitelist_commands import get_collection_commands


class RetryFakeConnection:
    def send_command(self, command, read_timeout=15):
        return "ok"

    def disconnect(self):
        pass


def _router_device():
    return {
        "device_name": "HQ-R1",
        "device_type": "cisco_ios",
        "model": "IOSv (EVE-NG)",
        "role": "总部核心路由器",
        "mgmt_ip": "192.0.2.1",
    }


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


def test_collect_device_retries_transient_connection_failure(monkeypatch):
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise NetmikoTimeoutException("No existing session")
        return RetryFakeConnection()

    monkeypatch.setattr(collector_ssh, "ConnectHandler", fake_connect)
    monkeypatch.setattr(collector_ssh.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("SSH_CONNECT_RETRIES", "1")
    monkeypatch.setenv("SSH_RETRY_DELAY_SECONDS", "0")

    results = collector_ssh.collect_device(_router_device(), "admin", "password")

    assert len(calls) == 2
    assert all(result["status"] == "success" for result in results)


def test_collect_device_does_not_retry_authentication_failure(monkeypatch):
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        raise NetmikoAuthenticationException("bad password")

    monkeypatch.setattr(collector_ssh, "ConnectHandler", fake_connect)
    monkeypatch.setenv("SSH_CONNECT_RETRIES", "3")

    results = collector_ssh.collect_device(_router_device(), "admin", "wrong")

    assert len(calls) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["error_code"] == "ssh_connection_failed"
