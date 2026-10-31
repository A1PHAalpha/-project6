from collector import db
from rag_engine.answer_validation import validate_answer


def test_multisegment_command_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    answer = {
        "recheck_method": "ping 10.10.10.100",
        "fix_suggestions": [
            {
                "action": "启用接口",
                "command": "interface Ethernet0/0.10; no shutdown; interface Ethernet0/0.20",
                "requires_manual_confirmation": True,
            }
        ],
    }

    result = validate_answer(answer)

    suggestion = result["fix_suggestions"][0]
    assert suggestion["command"] is None
    assert "需人工拆分" in suggestion["action"]


def test_single_command_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    answer = {
        "recheck_method": "ping 10.10.10.100",
        "fix_suggestions": [
            {
                "action": "查看接口",
                "command": "show ip interface brief",
                "requires_manual_confirmation": True,
            }
        ],
    }

    result = validate_answer(answer)

    assert result["fix_suggestions"][0]["command"] == "show ip interface brief"


def test_recheck_target_uses_server_inventory_address(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    db.create_managed_device(
        {
            "name": "HQ-SRV",
            "site": "总部",
            "role": "总部服务器终端",
            "deviceType": "linux",
            "managementIp": "10.10.10.100",
        }
    )
    db.create_managed_device(
        {
            "name": "BR-A-R2",
            "site": "分支A",
            "role": "分支A路由器",
            "deviceType": "cisco_ios",
            "managementIp": "192.168.249.12",
        }
    )
    answer = {
        "recheck_method": "在 BR-A-R2 上执行 ping 192.168.249.12（总部服务器IP）验证连通性",
        "fix_suggestions": [],
    }

    result = validate_answer(answer)

    assert "10.10.10.100" in result["recheck_method"]
    assert "192.168.249.12" not in result["recheck_method"]
