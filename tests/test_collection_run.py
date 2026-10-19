"""Collection summary tests without real device connections."""
from collector import run_collect


def test_run_collection_returns_full_summary(monkeypatch):
    devices = [
        {"device_name": "HQ-SRV", "device_type": "linux", "mgmt_ip": "10.0.0.1"},
        {"device_name": "BR-PC", "device_type": "linux", "mgmt_ip": "10.0.0.2"},
    ]
    monkeypatch.setenv("DEVICE_USERNAME", "test-user")
    monkeypatch.setenv("DEVICE_PASSWORD", "test-password")
    monkeypatch.setattr(run_collect.db, "init_db", lambda: None)
    monkeypatch.setattr(run_collect.db, "load_inventory", lambda _path: devices)
    monkeypatch.setattr(
        run_collect,
        "collect_all",
        lambda selected, username, password: [
            {"results": [{"status": "success"}, {"status": "failed"}]}
        ],
    )
    monkeypatch.setattr(
        run_collect,
        "collect_all_snmp",
        lambda selected, community: [
            {"result": {"status": "success"}},
            {"result": {"status": "failed"}},
        ],
    )
    monkeypatch.setattr(run_collect, "record_connectivity_test", lambda *args, **kwargs: {})

    result = run_collect.run_collection()

    assert result["mode"] == "full"
    assert result["device_count"] == 2
    assert result["ssh_commands_success"] == 1
    assert result["ssh_commands_failed"] == 1
    assert result["snmp_devices_success"] == 1
    assert result["snmp_devices_failed"] == 1
    assert result["connectivity_tests"] == 1
