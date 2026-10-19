"""二期会话、检索审计、工具调用和 v2 -> v3 迁移测试。"""
import json
import sqlite3
from pathlib import Path

import pytest

from collector import db
from ops_tools import invoke_tool


@pytest.fixture
def phase2_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ops.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    return db_path


def _seed_interface_evidence():
    device = db.upsert_device(
        {
            "device_name": "HQ-R1",
            "site": "总部",
            "device_type": "cisco_ios",
            "mgmt_ip": "10.0.0.1",
        }
    )
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
    bundle = db.get_current_device_bundle("HQ-R1")
    return device, bundle


def _save_fake_diagnosis(bundle, session_id=None):
    observation = bundle["observations"][0]
    evidence = [
        {
            "device_name": "HQ-R1",
            "collected_at": observation["observed_at_utc"],
            "status": "success",
            "summary": "Ethernet0/0 接口 up/up",
            "observation_id": observation["observation_id"],
            "item_id": observation["item_id"],
            "run_id": bundle["result"]["run_id"],
            "state_type": observation["state_type"],
        }
    ]
    answer = {
        "fault_phenomenon": "接口状态正常",
        "evidence_used": [
            {
                "observation_id": observation["observation_id"],
                "device_name": "HQ-R1",
                "collected_at": observation["observed_at_utc"],
                "finding": "接口 up/up",
            }
        ],
        "possible_causes": [
            {
                "cause": "上层路由问题",
                "kb_refs": ["KB-ROUTE-001"],
                "confidence": "medium",
            }
        ],
        "check_steps": ["show ip route"],
        "fix_suggestions": [
            {
                "action": "人工核对默认路由",
                "requires_manual_confirmation": True,
                "kb_refs": ["KB-ROUTE-001"],
            }
        ],
        "recheck_method": "ping 10.0.0.2",
        "missing_information": "无",
    }
    return db.save_diagnosis_run(
        question="总部路由器接口是否正常？",
        model_id="test-model",
        prompt_template_id="diagnosis_v1",
        prompt_version={
            "system_prompt": "system",
            "user_template": "user {question}",
        },
        llm_params={
            "model": "test-model",
            "temperature": 0.2,
            "_provider": "test-provider",
            "_request_id": "req-1",
            "_usage": {"prompt_tokens": 20, "completion_tokens": 10},
        },
        answer=answer,
        evidence=evidence,
        retrieved_docs=[
            {
                "id": "KB-ROUTE-001",
                "title": "缺少默认路由",
                "category": "路由",
                "applicable_device": "Cisco IOS 路由器",
                "version": "IOS 15.x",
                "source": "test-source",
                "content": "检查 show ip route",
                "distance": 0.12,
            }
        ],
        use_kb=True,
        kb_status="success",
        kb_error=None,
        response_time_ms=80,
        total_response_time_ms=95,
        context_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user question"},
        ],
        requested_by="pytest",
        session_id=session_id,
    )


def test_phase2_schema_seeds_tools_and_knowledge_chunks(phase2_db):
    with db.get_conn() as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        kb_count = conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]

    assert version == 4
    assert {
        "agent_sessions",
        "agent_messages",
        "context_snapshots",
        "tool_registry",
        "tool_calls",
        "tool_call_results",
        "model_calls",
        "retrieval_queries",
        "retrieval_hits",
        "diagnosis_conclusions",
        "evidence_links",
        "change_requests",
        "change_steps",
        "change_approvals",
        "change_events",
        "command_executions",
        "physical_actions",
        "config_backups",
        "rechecks",
    } <= tables
    assert kb_count == 26
    assert {tool["tool_name"] for tool in db.list_tools()} == {
        "query_device_state",
        "query_collection_history",
        "run_readonly_collection",
    }


def test_diagnosis_audit_chain_is_reconstructable(phase2_db):
    _device, bundle = _seed_interface_evidence()

    first = _save_fake_diagnosis(bundle)
    second = _save_fake_diagnosis(bundle, session_id=first["session_id"])
    detail = db.get_diagnosis_detail(first["diagnosis_id"])

    assert first["session_id"] == second["session_id"]
    assert detail["retrieved_docs"][0]["id"] == "KB-ROUTE-001"
    assert detail["retrieved_docs"][0]["content"] == "检查 show ip route"
    assert detail["conclusion"]["fault_phenomenon"] == "接口状态正常"
    assert {link["link_type"] for link in detail["evidence_links"]} == {
        "device_evidence",
        "knowledge",
    }
    assert len(detail["session"]["messages"]) == 4
    assert len(detail["session"]["model_calls"]) == 2

    session = db.get_agent_session(first["session_id"])
    assert [message["role"] for message in session["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert session["context_snapshots"][0]["prompt_hash"]
    assert session["model_calls"][0]["request_id"] == "req-1"


def test_readonly_tool_call_records_result(phase2_db):
    _device, bundle = _seed_interface_evidence()

    result = invoke_tool(
        "query_device_state",
        {"device_names": ["HQ-R1"], "state_types": ["interface"]},
        requested_by="pytest",
    )

    assert result["status"] == "success"
    assert result["result"]["device_count"] == 1
    assert result["result"]["devices"][0]["observations"][0]["state_type"] == "interface"
    stored = db.get_tool_call(result["tool_call_id"])
    assert stored["tool_name"] == "query_device_state"
    assert stored["status"] == "success"
    assert stored["result"]["result"]["device_count"] == 1
    assert stored["arguments"]["device_names"] == ["HQ-R1"]


def test_tool_collection_is_linked_to_tool_call(phase2_db, monkeypatch):
    import collector.run_collect as run_collect

    run_id = db.start_collection_run(
        trigger_type="llm_tool",
        scope_type="tool",
        requested_by="pytest",
    )
    monkeypatch.setattr(
        run_collect,
        "run_collection",
        lambda _devices, **kwargs: {
            "run_id": run_id,
            "trigger_type": kwargs.get("trigger_type"),
            "requested_devices": ["HQ-R1"],
            "source_ref": kwargs.get("source_ref"),
        },
    )

    result = invoke_tool(
        "run_readonly_collection",
        {"device_names": ["HQ-R1"]},
        requested_by="pytest",
    )

    assert result["status"] == "success"
    assert result["collection_run_id"] == run_id
    stored = db.get_tool_call(result["tool_call_id"])
    assert stored["result"]["collection_run_id"] == run_id
    assert stored["result"]["result"]["trigger_type"] == "llm_tool"


def test_v2_database_migrates_and_backfills_legacy_diagnosis(tmp_path, monkeypatch):
    db_path = tmp_path / "v2.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(db.BASE_SCHEMA)
    conn.execute(
        "INSERT INTO schema_migrations VALUES (1, 'phase1_asset_collection_state', ?)",
        (db.now_iso(),),
    )
    conn.execute(
        "INSERT INTO schema_migrations VALUES (2, 'normalize_utc_timestamps', ?)",
        (db.now_iso(),),
    )
    conn.execute(
        """INSERT INTO diagnosis_records
           (created_at, question, model_id, prompt_template_id, llm_params_json,
            retrieved_doc_ids_json, evidence_json, answer_json, use_kb, response_time_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 10)""",
        (
            "2026-10-20T00:00:00+00:00",
            "历史问题",
            "legacy-model",
            "diagnosis_v1",
            json.dumps({"model": "legacy-model"}),
            json.dumps(["KB-ROUTE-001"]),
            json.dumps(
                [
                    {
                        "device_name": "HQ-R1",
                        "collected_at": "2026-10-20T00:00:00+00:00",
                        "status": "success",
                        "summary": "历史证据",
                    }
                ]
            ),
            json.dumps(
                {
                    "fault_phenomenon": "历史故障",
                    "possible_causes": [{"cause": "历史原因"}],
                    "missing_information": "无",
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()

    with db.get_conn() as migrated:
        assert migrated.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 4
        record = migrated.execute(
            "SELECT session_id, conclusion_id FROM diagnosis_records WHERE id=1"
        ).fetchone()
        assert record["session_id"]
        assert record["conclusion_id"]
        assert migrated.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0] == 2
        assert migrated.execute("SELECT COUNT(*) FROM retrieval_hits").fetchone()[0] == 1

    detail = db.get_diagnosis_detail(1)
    assert detail["retrieved_docs"][0]["id"] == "KB-ROUTE-001"
    assert detail["conclusion"]["root_cause"] == "历史原因"


def test_human_change_workflow_is_append_only_and_complete(phase2_db, tmp_path, monkeypatch):
    _device, bundle = _seed_interface_evidence()
    diagnosis = _save_fake_diagnosis(bundle)

    change = db.create_change_request(
        diagnosis_id=diagnosis["diagnosis_id"],
        device_name="HQ-R1",
        change_type="mixed",
        title="修复 HQ-R1 路由",
        description="补充默认路由并检查上联链路",
        operator="alice",
        status="pending_approval",
    )
    assert change["status"] == "pending_approval"

    approval = db.approve_change(
        change["change_id"],
        approver="bob",
        decision="approved",
        comment="批准在维护窗口操作",
    )
    assert approval["status"] == "approved"
    assert db.start_change(change["change_id"], operator="alice")["status"] == "executing"
    backup = db.record_config_backup(
        change["change_id"],
        content="hostname HQ-R1\n",
        storage_ref="reports/backups/HQ-R1-test.cfg",
        sha256=None,
        operator="alice",
    )
    assert backup["sha256"]

    software = db.add_change_step(
        change["change_id"],
        step_type="software",
        source_type="llm",
        title="补充默认路由",
        command_text="ip route 0.0.0.0 0.0.0.0 172.16.1.1",
        expected_result="路由表出现 S* 默认路由",
        operator="alice",
    )
    physical = db.add_change_step(
        change["change_id"],
        step_type="physical",
        source_type="human",
        title="检查上联线缆",
        operator="alice",
    )
    execution = db.record_command_execution(
        change["change_id"],
        software["step_id"],
        command_text="ip route 0.0.0.0 0.0.0.0 172.16.1.1",
        output="configured",
        exit_code=0,
        operator="alice",
        result_summary="配置已由人工执行",
    )
    assert execution["step_status"] == "success"
    physical_result = db.record_physical_action(
        change["change_id"],
        physical["step_id"],
        action_type="cable",
        description="重新插拔 GigabitEthernet0/1 线缆",
        result="接口物理状态恢复",
        evidence_text="现场照片已归档",
        operator="alice",
    )
    assert physical_result["status"] == "success"
    recheck = db.record_recheck(
        change["change_id"],
        collection_run_id=None,
        source_device_id=None,
        source_device_name="HQ-R1",
        target="10.10.10.100",
        check_type="ping",
        expected={"reachable": True},
        actual={"reachable": True},
        pass_fail="pass",
        operator="alice",
    )
    assert recheck["pass_fail"] == "pass"
    completed = db.complete_change(change["change_id"], operator="alice")

    detail = db.get_change(change["change_id"])
    assert completed["status"] == "completed"
    assert detail["change"]["status"] == "completed"
    assert len(detail["steps"]) == 2
    assert len(detail["approvals"]) == 1
    assert len(detail["command_executions"]) == 1
    assert len(detail["physical_actions"]) == 1
    assert len(detail["config_backups"]) == 1
    assert len(detail["rechecks"]) == 1
    assert [event["event_type"] for event in detail["events"]] == [
        "created",
        "approval_recorded",
        "execution_started",
        "config_backup_recorded",
        "step_added",
        "step_added",
        "command_executed",
        "physical_action_recorded",
        "recheck_recorded",
        "change_finished",
    ]
    assert db.get_diagnosis_detail(diagnosis["diagnosis_id"])["changes"][0]["change_id"] == change["change_id"]

    from webapp import report_export

    monkeypatch.setattr(report_export, "REPORT_DIR", str(tmp_path / "reports"))
    report_path = report_export.export_diagnosis_report(diagnosis["diagnosis_id"])
    report_text = Path(report_path).read_text(encoding="utf-8")
    assert "人工变更与复测" in report_text
    assert "修复 HQ-R1 路由" in report_text
