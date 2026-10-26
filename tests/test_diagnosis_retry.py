import json

import rag_engine.diagnosis_engine as engine


def _evidence(count=20):
    return [
        {
            "device_name": "HQ-R1",
            "collected_at": "2026-10-26T00:00:00+00:00",
            "status": "success",
            "command": "show ip interface brief",
            "summary": f"接口 down {i}",
            "observation_id": f"obs_{i}",
            "run_id": "run_1",
            "state_type": "interface",
            "priority": 0 if i < 3 else 6,
        }
        for i in range(count)
    ]


def _valid_answer():
    return {
        "fault_phenomenon": "链路 down",
        "evidence_used": [
            {
                "observation_id": "obs_0",
                "device_name": "HQ-R1",
                "collected_at": "2026-10-26T00:00:00+00:00",
                "finding": "down",
            }
        ],
        "possible_causes": [
            {"cause": "链路故障", "kb_refs": [], "confidence": "high"}
        ],
        "check_steps": ["show interfaces"],
        "fix_suggestions": [],
        "recheck_method": "ping",
        "missing_information": "无",
    }


def _saved_result():
    return {
        "diagnosis_id": 1,
        "session_id": "s",
        "conclusion_id": "c",
        "context_snapshot_id": "ctx",
        "retrieval_query_id": "q",
        "model_call_id": "m",
    }


def test_truncated_output_retries_with_compact_evidence(monkeypatch):
    calls = []

    def fake_chat(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return '{"fault_phenomenon": "截断', 10, {
                "model": "fake",
                "_finish_reason": "length",
                "_status": "success",
            }
        return json.dumps(_valid_answer(), ensure_ascii=False), 20, {
            "model": "fake",
            "_finish_reason": "stop",
            "_status": "success",
        }

    monkeypatch.setattr(engine, "chat_completion", fake_chat)
    monkeypatch.setattr(engine, "extract_relevant_devices", lambda q: ["HQ-R1"])
    monkeypatch.setattr(engine, "build_evidence_for_devices", lambda names: _evidence())
    monkeypatch.setattr(engine.db, "save_diagnosis_run", lambda **kwargs: _saved_result())

    result = engine.diagnose("HQ-R1 链路正常吗", use_kb=False)

    assert len(calls) == 2
    assert result["answer"]["fault_phenomenon"] == "链路 down"
    assert result["model_status"] == "success"
    assert result["evidence"] and len(result["evidence"]) == 20
    evidence_lines = [
        line for line in calls[1][-1]["content"].splitlines() if line.startswith("- 设备:")
    ]
    assert len(evidence_lines) == engine.RETRY_EVIDENCE_ITEMS


def test_retry_failure_marks_model_error(monkeypatch):
    saved = {}

    def fake_chat(messages, **kwargs):
        return "not json", 5, {
            "model": "fake",
            "_finish_reason": "stop",
            "_status": "success",
        }

    def fake_save(**kwargs):
        saved.update(kwargs)
        return _saved_result()

    monkeypatch.setattr(engine, "chat_completion", fake_chat)
    monkeypatch.setattr(engine, "extract_relevant_devices", lambda q: ["HQ-R1"])
    monkeypatch.setattr(engine, "build_evidence_for_devices", lambda names: _evidence())
    monkeypatch.setattr(engine.db, "save_diagnosis_run", fake_save)

    result = engine.diagnose("HQ-R1 链路正常吗", use_kb=False)

    assert result["model_status"] == "error"
    assert result["answer"]["fault_phenomenon"] == "模型输出格式解析失败"
    assert saved["model_status"] == "error"
    assert saved["model_error_code"] == "LLM_OUTPUT_INVALID"
    assert "stop" in (saved["model_error_message"] or "")
