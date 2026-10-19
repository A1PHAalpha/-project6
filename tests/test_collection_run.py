"""Collection orchestration tests without real device connections."""
from collector import db, run_collect


def test_run_collection_creates_run_results_and_items(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    devices = [
        db.upsert_device(
            {
                "device_name": "HQ-R1",
                "site": "总部",
                "device_type": "cisco_ios",
                "mgmt_ip": "10.0.0.1",
                "snmp_port": 161,
            }
        )
    ]
    monkeypatch.setenv("DEVICE_USERNAME", "test-user")
    monkeypatch.setenv("DEVICE_PASSWORD", "test-password")
    monkeypatch.setattr(
        run_collect.db,
        "load_inventory_with_version",
        lambda _path: {"inventory_version_id": None, "devices": devices},
    )
    monkeypatch.setattr(
        run_collect,
        "collect_device",
        lambda _device, _username, _password: [
            {
                "item": "interface_status",
                "command": "show ip interface brief",
                "status": "success",
                "source": "ssh",
                "capture_type": "interface",
                "started_at": "2026-10-20T00:00:00+00:00",
                "finished_at": "2026-10-20T00:00:01+00:00",
                "raw_output": "Ethernet0/0 10.0.0.1 YES NVRAM up up",
                "parsed": {},
            },
            {
                "item": "route_table",
                "command": "show ip route",
                "status": "failed",
                "source": "ssh",
                "capture_type": "route",
                "started_at": "2026-10-20T00:00:00+00:00",
                "finished_at": "2026-10-20T00:00:01+00:00",
                "fail_reason": "timeout",
            },
        ],
    )
    monkeypatch.setattr(
        run_collect,
        "collect_device_snmp",
        lambda _device, _community: {
            "status": "success",
            "source": "snmp",
            "capture_type": "system",
            "command": "SNMP:ifTable+sysUpTime",
            "started_at": "2026-10-20T00:00:00+00:00",
            "finished_at": "2026-10-20T00:00:02+00:00",
            "raw_output": "{'sys_uptime': '10'}",
            "parsed": {
                "sys_uptime": "10",
                "interfaces": [
                    {
                        "index": "1",
                        "descr": "Ethernet0/0",
                        "admin_status": "up",
                        "oper_status": "up",
                    }
                ],
            },
        },
    )

    result = run_collect.run_collection(trigger_type="manual", requested_by="test")

    assert result["mode"] == "full"
    assert result["run_id"].startswith("run_")
    assert result["ssh_commands_success"] == 1
    assert result["ssh_commands_failed"] == 1
    assert result["snmp_devices_success"] == 1
    assert result["connectivity_tests"] == 0

    with db.get_conn() as conn:
        assert conn.execute(
            "SELECT status FROM collection_runs WHERE run_id=?", (result["run_id"],)
        ).fetchone()[0] == "partial"
        assert conn.execute(
            "SELECT COUNT(*) FROM collection_items WHERE result_id IN "
            "(SELECT result_id FROM device_capture_results WHERE run_id=?)",
            (result["run_id"],),
        ).fetchone()[0] == 3
