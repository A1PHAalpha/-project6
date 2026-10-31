from pathlib import Path

from collector import db
from scripts.verify_acceptance import run_audit

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_audit_reads_database_and_reports_device_count(tmp_path, monkeypatch):
    db_path = tmp_path / "ops.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    db.create_managed_device(
        {
            "name": "HQ-R1",
            "site": "总部",
            "role": "总部核心路由器",
            "model": "IOSv (EVE-NG)",
            "deviceType": "cisco_ios",
            "managementIp": "10.0.0.1",
        }
    )
    db.create_managed_device(
        {
            "name": "HQ-SW1",
            "site": "总部",
            "role": "总部核心交换机",
            "model": "IOSvL2 (EVE-NG)",
            "deviceType": "cisco_ios",
            "managementIp": "10.0.0.2",
        }
    )

    payload = run_audit(
        db_path,
        PROJECT_ROOT / "knowledge_base" / "articles.json",
        tmp_path / "reports",
    )

    assert payload["summary"]["devices"] >= 2
    assert payload["summary"]["routers"] == 1
    assert payload["summary"]["switches"] == 1
    assert payload["summary"]["collection_runs"] == 0
    assert any(item["id"] == "N1-devices" for item in payload["checks"])
    assert any(item["status"] in ("PASS", "WARN", "FAIL") for item in payload["checks"])
