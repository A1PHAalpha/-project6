from collector import db
from webapp.api.v1.router import _session_detail_dto


def test_tool_call_dto_exposes_device_name(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    saved = db.save_diagnosis_run(
        question="检查 HQ-R1",
        model_id="test",
        prompt_template_id="diagnosis_v2",
        prompt_version=None,
        llm_params={},
        answer={"fault_phenomenon": "测试", "possible_causes": []},
        evidence=[],
        retrieved_docs=[],
        use_kb=False,
        kb_status="disabled",
        kb_error=None,
        response_time_ms=1,
        total_response_time_ms=1,
        context_messages=[],
        session_id=None,
    )
    call = db.start_tool_call(
        "query_device_state",
        {"device_names": ["HQ-R1"]},
        session_id=saved["session_id"],
    )
    db.finish_tool_call(call["tool_call_id"], status="success", result={"ok": True})
    dto = _session_detail_dto(saved["session_id"])
    assert dto["toolCalls"][0]["deviceName"] == "HQ-R1"
