"""一期数据库结构、采集批次和迁移测试。"""
import json
import sqlite3

import pytest

from collector import db
from rag_engine.evidence_builder import build_evidence_for_devices


@pytest.fixture
def phase1_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    yield


def _device(name="HQ-R1", site="总部"):
    return {
        "device_name": name,
        "site": site,
        "device_type": "cisco_ios",
        "mgmt_ip": "10.0.0.1",
        "snmp_port": 161,
        "interfaces": ["Ethernet0/0", "Ethernet0/1"],
    }


def test_batch_collection_creates_run_results_items_and_observations(phase1_db):
    db.upsert_device(_device())
    batch = db.insert_collection_batch(
        [
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": (
                    "Ethernet0/0 10.0.0.1 YES NVRAM up up\n"
                    "Ethernet0/1 unassigned YES NVRAM administratively down down\n"
                ),
                "collected_at": "2026-10-20T00:00:00+00:00",
            },
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show ip route",
                "raw_output": "C 10.0.0.0/24 is directly connected, Ethernet0/0\n",
                "collected_at": "2026-10-20T00:00:01+00:00",
            },
        ],
        trigger_type="synthetic",
    )

    assert batch["run_id"].startswith("run_")
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM device_capture_results").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM state_observations").fetchone()[0] >= 3
        assert conn.execute("SELECT COUNT(*) FROM command_policies").fetchone()[0] > 0

    bundle = db.get_current_device_bundle("HQ-R1")
    assert bundle["result"]["reachability"] == "reachable"
    assert {item["state_type"] for item in bundle["observations"]} >= {"interface", "route"}
    evidence = build_evidence_for_devices(["HQ-R1"])
    assert evidence
    assert any(item.get("observation_id") for item in evidence)
    assert all(item["device_name"] == "HQ-R1" for item in evidence)
    assert db.get_device_collection_history("HQ-R1")[0]["command"] == "show ip route"
    assert db.get_all_latest_status()


def test_failed_latest_capture_does_not_reuse_old_interface_state(phase1_db):
    db.upsert_device(_device())
    db.insert_collection_batch(
        [
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": "Ethernet0/0 10.0.0.1 YES NVRAM up up\n",
                "collected_at": "2026-10-20T00:00:00+00:00",
            }
        ],
        trigger_type="synthetic",
    )
    db.insert_collection_batch(
        [
            {
                "device_name": "HQ-R1",
                "status": "failed",
                "command": "show ip interface brief",
                "fail_reason": "SSH timeout",
                "collected_at": "2026-10-20T00:01:00+00:00",
            }
        ],
        trigger_type="synthetic",
    )

    bundle = db.get_current_device_bundle("HQ-R1")
    assert bundle["result"]["reachability"] == "unreachable"
    assert [item["state_type"] for item in bundle["observations"]] == ["reachability"]
    assert bundle["observations"][0]["payload"]["reachable"] is False
    evidence = build_evidence_for_devices(["HQ-R1"])
    assert [item["command"] for item in evidence] == ["device_reachability"]
    current = db.get_all_latest_status()
    assert any(
        item["device_name"] == "HQ-R1" and item["command"] == "device_reachability"
        for item in current
    )


def test_latest_result_is_compared_by_real_time_not_text(phase1_db):
    db.upsert_device(_device())
    old = db.insert_collection_batch(
        [
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show ip interface brief",
                "raw_output": "Ethernet0/0 10.0.0.1 YES NVRAM up up\n",
                "collected_at": "2026-10-20T00:00:00+08:00",
            }
        ],
        trigger_type="synthetic",
    )
    new = db.insert_collection_batch(
        [
            {
                "device_name": "HQ-R1",
                "status": "failed",
                "command": "show ip interface brief",
                "fail_reason": "newer UTC failure",
                "collected_at": "2026-10-19T17:00:00+00:00",
            }
        ],
        trigger_type="synthetic",
    )

    assert old["run_id"] != new["run_id"]
    bundle = db.get_current_device_bundle("HQ-R1")
    assert bundle["result"]["run_id"] == new["run_id"]
    with db.get_conn() as conn:
        stored = conn.execute(
            "SELECT published_at_utc FROM collection_runs WHERE run_id=?",
            (new["run_id"],),
        ).fetchone()[0]
    assert stored.endswith("+00:00")


def test_cdp_observation_builds_topology_snapshot(phase1_db):
    db.upsert_device(_device("BR-A-SW2", "分支A"))
    db.upsert_device(_device("HQ-R1", "总部"))
    batch = db.insert_collection_batch(
        [
            {
                "device_name": "BR-A-SW2",
                "status": "success",
                "command": "show cdp neighbors",
                "raw_output": (
                    "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\n"
                    "HQ-R1            Eth 0/0           171        R B         Linux Uni Eth 0/0\n"
                ),
                "collected_at": "2026-10-20T00:00:00+00:00",
            }
        ],
        trigger_type="synthetic",
    )

    snapshot = db.get_topology_snapshot(batch["run_id"])
    assert snapshot is not None
    assert len(snapshot["edges"]) == 1
    labels = {node["label"] for node in snapshot["nodes"]}
    assert {"BR-A-SW2", "HQ-R1"} <= labels


def test_legacy_database_is_backed_up_and_migrated(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_path)
    conn.executescript(
        """
        CREATE TABLE devices (
            device_name TEXT PRIMARY KEY,
            role TEXT,
            site TEXT,
            vendor TEXT,
            model TEXT,
            mgmt_ip TEXT,
            device_type TEXT,
            raw_json TEXT
        );
        CREATE TABLE collection_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            status TEXT NOT NULL,
            fail_reason TEXT,
            command TEXT,
            raw_output TEXT,
            parsed_json TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "HQ-R1",
            "总部路由器",
            "总部",
            "Cisco",
            "IOSv",
            "10.0.0.1",
            "cisco_ios",
            json.dumps({"interfaces": ["Ethernet0/0"]}),
        ),
    )
    conn.execute(
        """INSERT INTO collection_records
           (device_name, collected_at, status, fail_reason, command, raw_output, parsed_json)
           VALUES (?, ?, 'success', NULL, ?, ?, ?)""",
        (
            "HQ-R1",
            "2026-10-20T00:00:00+00:00",
            "show ip interface brief",
            "Ethernet0/0 10.0.0.1 YES NVRAM up up",
            json.dumps(json.dumps({"line_count": 1})),
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", str(legacy_path))
    db.init_db()

    with db.get_conn() as migrated:
        assert migrated.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='devices_legacy'"
        ).fetchone()[0] == 1
        assert migrated.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='collection_records_legacy'"
        ).fetchone()[0] == 1
        assert migrated.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
        assert migrated.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1
        assert migrated.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0] == 1
        parsed = migrated.execute("SELECT parsed_json FROM collection_items").fetchone()[0]
        assert json.loads(parsed) == {"line_count": 1}

    assert db.get_current_device_status()[0]["reachability"] == "reachable"
