"""Web 变更接口的直接调用测试，避免依赖异步 TestClient 运行环境。"""
from collector import db
from webapp import main as web_main


def test_change_api_flow(tmp_path, monkeypatch):
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
    diagnosis_id = db.insert_diagnosis(
        "测试问题",
        "test-model",
        "diagnosis_v1",
        {},
        [],
        [],
        {"fault_phenomenon": "测试", "possible_causes": [], "missing_information": "无"},
        use_kb=0,
        response_time_ms=1,
    )

    created = web_main.api_create_change(
        web_main.ChangeCreateRequest(
            diagnosis_id=diagnosis_id,
            device_name="HQ-R1",
            change_type="software",
            title="测试变更",
            operator="alice",
        )
    )
    assert created["status"] == "pending_approval"
    change_id = created["change_id"]

    approved = web_main.api_approve_change(
        change_id,
        web_main.ChangeApprovalRequest(approver="bob", decision="approved"),
    )
    assert approved["status"] == "approved"
    db.record_config_backup(
        change_id,
        content="hostname HQ-R1\n",
        storage_ref="test.cfg",
        sha256=None,
        operator="alice",
    )
    step = web_main.api_add_change_step(
        change_id,
        web_main.ChangeStepRequest(
            step_type="software",
            title="执行命令",
            command_text="show ip route",
            operator="alice",
        ),
    )
    execution = web_main.api_record_command(
        change_id,
        step["step_id"],
        web_main.CommandExecutionRequest(
            command_text="show ip route",
            output="S* 0.0.0.0/0",
            exit_code=0,
            operator="alice",
        ),
    )
    assert execution["step_status"] == "success"
    detail = web_main.api_get_change(change_id)
    assert detail["change"]["status"] == "executing"
    assert len(detail["steps"]) == 1
    listed = web_main.api_list_changes()
    assert listed[0]["change_id"] == change_id
