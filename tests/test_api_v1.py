"""Console v1 API, inventory authority, and terminal audit tests."""
import json

import pytest

import app_bootstrap
from collector import db
from control.terminal_service import TerminalError, terminal_manager
from webapp import api_v1, environment_settings


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()


def test_database_inventory_is_not_overwritten_by_seed_file(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "device_name": "SEED-R1",
                        "site": "总部",
                        "device_type": "cisco_ios",
                        "mgmt_ip": "192.0.2.1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_bootstrap, "INVENTORY_PATH", inventory_path)

    first = app_bootstrap.ensure_database()
    assert first["inventory_loaded"] is True
    seeded = db.list_active_devices()[0]
    db.update_managed_device(seeded["device_id"], {"name": "EDITED-R1"})
    db.create_managed_device(
        {
            "name": "CONSOLE-R2",
            "site": "分支",
            "deviceType": "cisco_ios",
            "managementIp": "192.0.2.2",
        }
    )

    second = app_bootstrap.ensure_database()
    names = {device["device_name"] for device in db.list_active_devices()}
    assert second["inventory_loaded"] is False
    assert names == {"EDITED-R1", "CONSOLE-R2"}


def test_device_create_paginate_and_soft_archive(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    created = api_v1.create_device(
        api_v1.DeviceCreate(
            name="LAB-R1",
            site="实验室",
            role="核心路由器",
            vendor="Cisco",
            model="IOSv",
            device_type="cisco_ios",
            mgmt_ip="192.0.2.10",
            ssh_port=22,
            snmp_port=161,
            interfaces=["GigabitEthernet0/0"],
        ),
        request=None,
    )
    assert created["reachability"] == "unknown"
    assert created["inventoryVersionId"]
    with db.get_conn() as conn:
        snapshot = conn.execute(
            """SELECT COUNT(*) FROM inventory_items
               WHERE version_id=?""",
            (created["inventoryVersionId"],),
        ).fetchone()[0]
    assert snapshot == 1
    page = api_v1.list_devices(page=1, page_size=10)
    assert page["total"] == 1
    assert page["items"][0]["facts"][0]["tone"] == "neutral"

    result = api_v1.archive_devices(
        api_v1.ArchiveRequest(deviceIds=[created["id"]], reason="test")
    )
    assert result["archived"] == 1
    assert result["inventoryVersionId"] != created["inventoryVersionId"]
    assert db.get_device(created["id"])["lifecycle_status"] == "retired"
    assert api_v1.list_devices(page=1, page_size=10)["total"] == 0


def test_overview_and_knowledge_dto(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    db.create_managed_device(
        {
            "name": "HQ-SRV",
            "site": "总部",
            "deviceType": "linux",
            "managementIp": "192.0.2.20",
        }
    )
    overview = api_v1.get_overview()
    assert overview["devices"]["unknown"] == 1
    assert overview["topology"]["nodes"][0]["summary"] == "未采集"
    assert api_v1.knowledge_articles(page=1, page_size=5)["total"] == 26
    assert api_v1.get_collection_schedule()["frequency_minutes"] == 30


def test_terminal_requires_backup_and_confirmation_and_records_events(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    db.create_managed_device(
        {
            "name": "LAB-R1",
            "site": "实验室",
            "deviceType": "cisco_ios",
            "managementIp": "192.0.2.30",
        }
    )
    with pytest.raises(ValueError, match="BACKUP_ACKNOWLEDGEMENT_REQUIRED"):
        db.create_terminal_session(
            "LAB-R1",
            operator="tester",
            backup_acknowledged=False,
        )

    session = db.create_terminal_session(
        "LAB-R1",
        operator="tester",
        backup_acknowledged=True,
    )
    db.append_terminal_event(
        session["id"],
        event_type="command",
        source="manual",
        command_text="configure terminal",
    )
    with pytest.raises(TerminalError) as exc:
        terminal_manager.execute(
            session["id"],
            "interface Gi0/1",
            source="quick_action",
            confirmed=False,
        )
    assert exc.value.code == "QUICK_ACTION_CONFIRMATION_REQUIRED"
    detail = db.get_terminal_session(session["id"])
    assert detail["backupAcknowledgedAt"]
    assert [event["eventType"] for event in detail["events"]] == [
        "backup_notice",
        "session_open",
        "command",
    ]


def test_environment_save_masks_and_preserves_other_keys(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# existing\nLLM_MODEL=old-model\nCUSTOM_KEY=keep-me\n",
        encoding="utf-8",
    )
    result = environment_settings.save_settings(
        {
            "LLM_MODEL": "new-model",
            "LLM_API_KEY": "secret-value-123456",
            "FASTEMBED_CACHE_PATH": "./models/fastembed",
        },
        path=env_path,
    )
    text = env_path.read_text(encoding="utf-8")
    assert "CUSTOM_KEY=keep-me" in text
    assert "LLM_MODEL=new-model" in text
    assert "secret-value-123456" not in json.dumps(result, ensure_ascii=False)
    assert "•" in environment_settings.read_settings(env_path)["values"]["LLM_API_KEY"]
    assert result["needs_restart"] is True
