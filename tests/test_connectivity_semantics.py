from collector import db, run_collect
from collector.connectivity_test import collect_router_ping


def test_router_ping_parses_success_rate(monkeypatch):
    monkeypatch.setattr(
        "collector.collector_ssh.execute_ssh_command",
        lambda *_args, **_kwargs: (
            "Type escape sequence to abort.\n"
            "Sending 5, 100-byte ICMP Echos to 10.10.10.100, timeout is 2 seconds:\n"
            "!!!!!\n"
            "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/1/1 ms"
        ),
    )
    result = collect_router_ping(
        {
            "device_name": "HQ-R1",
            "device_type": "cisco_ios",
            "mgmt_ip": "192.168.249.11",
        },
        "10.10.10.100",
        "admin",
        "password",
    )
    assert result["status"] == "success"
    assert result["reachable"] is True
    assert result["parsed"]["success_rate"] == 100
    assert result["parsed"]["source_device"] == "HQ-R1"


def test_router_ping_marks_zero_percent_unreachable(monkeypatch):
    monkeypatch.setattr(
        "collector.collector_ssh.execute_ssh_command",
        lambda *_args, **_kwargs: (
            "Sending 5, 100-byte ICMP Echos to 10.10.10.100:\n"
            ".....\n"
            "Success rate is 0 percent (0/5)"
        ),
    )
    result = collect_router_ping(
        {
            "device_name": "HQ-R1",
            "device_type": "cisco_ios",
            "mgmt_ip": "192.168.249.11",
        },
        "10.10.10.100",
        "admin",
        "password",
    )
    assert result["status"] == "success"
    assert result["reachable"] is False
    assert result["parsed"]["success_rate"] == 0


def test_ping_command_success_but_unreachable_is_not_business_success(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    db.upsert_device(
        {
            "device_name": "HQ-SRV",
            "site": "总部",
            "device_type": "linux",
            "mgmt_ip": "10.10.10.100",
        }
    )
    db.upsert_device(
        {
            "device_name": "BR-A-PC",
            "site": "分支A",
            "device_type": "linux",
            "mgmt_ip": "10.20.10.100",
        }
    )
    monkeypatch.setenv("DEVICE_USERNAME", "test")
    monkeypatch.setenv("DEVICE_PASSWORD", "test")
    monkeypatch.setattr(
        run_collect,
        "collect_terminal_ping",
        lambda device, target, username, password: {
            "status": "success",
            "source": "ping",
            "capture_type": "ping",
            "command": f"ping -c 4 -W 2 {target}",
            "started_at": "2026-10-23T00:00:00+00:00",
            "finished_at": "2026-10-23T00:00:01+00:00",
            "raw_output": "4 packets transmitted, 0 received, 100% packet loss",
            "parsed": {"target_ip": target, "reachable": False},
            "observation_status": "unreachable",
        },
    )

    result = run_collect.run_collection(trigger_type="manual", requested_by="test")

    assert result["connectivity_tests"] == 1
    assert result["connectivity_successes"] == 0
    assert result["connectivity_failures"] == 1
    status = {row["device_name"]: row for row in db.get_current_device_status()}
    assert status["BR-A-PC"]["reachability"] == "unreachable"
