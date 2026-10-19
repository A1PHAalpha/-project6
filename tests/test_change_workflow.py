"""人工变更复测与真实采集批次联动测试。"""
import json

from collector import db
from control import change_workflow


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    db.upsert_device(
        {
            "device_name": "HQ-R1",
            "site": "总部",
            "device_type": "cisco_ios",
            "mgmt_ip": "10.0.0.1",
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
    diagnosis_id = db.insert_diagnosis(
        "分支A无法访问总部服务器",
        "test-model",
        "diagnosis_v1",
        {},
        [],
        [],
        {
            "fault_phenomenon": "测试",
            "possible_causes": [{"cause": "链路问题"}],
            "missing_information": "无",
        },
        use_kb=0,
        response_time_ms=10,
    )
    change = db.create_change_request(
        diagnosis_id=diagnosis_id,
        device_name="HQ-R1",
        change_type="mixed",
        title="测试人工变更",
        operator="alice",
        status="pending_approval",
    )
    db.approve_change(change["change_id"], approver="bob", decision="approved")
    db.record_config_backup(
        change["change_id"],
        content="hostname HQ-R1\n",
        storage_ref="test.cfg",
        sha256=None,
        operator="alice",
    )
    return change["change_id"]


def test_recheck_uses_collector_host_when_source_is_not_linux(tmp_path, monkeypatch):
    change_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        change_workflow,
        "ping",
        lambda target: {
            "success": True,
            "raw_output": f"Reply from {target}",
            "loss_rate": 0.0,
            "target_ip": target,
        },
    )

    result = change_workflow.run_change_recheck(
        change_id,
        source_device_name="HQ-R1",
        target_ip="10.10.10.100",
        operator="alice",
        expected_reachable=True,
    )

    assert result["pass_fail"] == "pass"
    assert result["actual"]["execution_location"] == "collector_host"
    detail = db.get_change(change_id)
    assert len(detail["rechecks"]) == 1
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM state_observations WHERE state_type='reachability'"
        ).fetchone()[0] == 1


def test_recheck_uses_source_device_execution(tmp_path, monkeypatch):
    change_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        change_workflow,
        "collect_terminal_ping",
        lambda device, target, username, password: {
            "status": "success",
            "source": "ping",
            "capture_type": "ping",
            "command": f"ping -c 4 -W 2 {target}",
            "started_at": "2026-10-20T00:00:00+00:00",
            "finished_at": "2026-10-20T00:00:01+00:00",
            "raw_output": "4 packets transmitted, 4 received, 0% packet loss",
            "parsed": {
                "target_ip": target,
                "reachable": True,
                "source_device": device["device_name"],
                "execution_location": "source_device",
            },
            "observation_status": "observed",
        },
    )
    monkeypatch.setenv("DEVICE_USERNAME", "test")
    monkeypatch.setenv("DEVICE_PASSWORD", "test")

    result = change_workflow.run_change_recheck(
        change_id,
        source_device_name="BR-A-PC",
        target_ip="10.10.10.100",
        operator="alice",
        expected_reachable=True,
    )

    assert result["pass_fail"] == "pass"
    assert result["actual"]["execution_location"] == "source_device"
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT actual_json FROM rechecks WHERE change_id=?",
            (change_id,),
        ).fetchone()
    assert json.loads(row[0])["source_device"] == "BR-A-PC"


def test_backup_change_config_persists_hash_and_event(tmp_path, monkeypatch):
    change_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("DEVICE_USERNAME", "test")
    monkeypatch.setenv("DEVICE_PASSWORD", "test")
    monkeypatch.setattr(
        change_workflow,
        "backup_running_config",
        lambda device, username, password: {
            "status": "success",
            "backup_path": "reports/backups/HQ-R1-test.cfg",
            "sha256": "abc123",
            "raw_config": "hostname HQ-R1\n",
            "device_name": device["device_name"],
        },
    )

    result = change_workflow.backup_change_config(change_id, "alice")

    assert result["status"] == "success"
    detail = db.get_change(change_id)
    assert any(backup["sha256"] == "abc123" for backup in detail["config_backups"])
    assert any(event["event_type"] == "config_backup_recorded" for event in detail["events"])


def test_backup_failure_is_recorded(tmp_path, monkeypatch):
    change_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("DEVICE_USERNAME", "test")
    monkeypatch.setenv("DEVICE_PASSWORD", "test")
    monkeypatch.setattr(
        change_workflow,
        "backup_running_config",
        lambda device, username, password: {
            "status": "failed",
            "reason": "SSH timeout",
        },
    )

    result = change_workflow.backup_change_config(change_id, "alice")

    assert result["status"] == "failed"
    detail = db.get_change(change_id)
    assert any(event["event_type"] == "config_backup_failed" for event in detail["events"])
