from collector import db, run_collect


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
