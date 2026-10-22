from pathlib import Path

import pytest

from collector import db
from webapp import report_export
from webapp.api.v1.router import ApiProblem, export_session_report


def _seed_diagnosis(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(report_export, "REPORT_DIR", str(tmp_path / "reports"))
    db.init_db()
    db.upsert_device(
        {
            "device_name": "HQ-R1",
            "site": "总部",
            "device_type": "cisco_ios",
            "mgmt_ip": "10.0.0.1",
        }
    )
    return db.save_diagnosis_run(
        question="链路是否正常",
        model_id="test-model",
        prompt_template_id="diagnosis_v2",
        prompt_version=None,
        llm_params={},
        answer={"fault_phenomenon": "测试现象", "possible_causes": []},
        evidence=[
            {
                "device_name": "HQ-R1",
                "run_id": "run_test",
                "collected_at": "2026-10-23T00:00:00+00:00",
                "status": "success",
                "summary": "接口 up",
            }
        ],
        retrieved_docs=[],
        use_kb=False,
        kb_status="disabled",
        kb_error=None,
        response_time_ms=1,
        total_response_time_ms=1,
        context_messages=[],
        session_id=None,
    )


def test_export_session_report_returns_latest_diagnosis(tmp_path, monkeypatch):
    saved = _seed_diagnosis(tmp_path, monkeypatch)
    response = export_session_report(saved["session_id"])
    assert Path(response.path).name == f"diagnosis_{saved['diagnosis_id']}.md"
    assert "故障诊断报告" in Path(response.path).read_text(encoding="utf-8")


def test_export_session_report_unknown_session_returns_404(tmp_path, monkeypatch):
    _seed_diagnosis(tmp_path, monkeypatch)
    with pytest.raises(ApiProblem) as exc:
        export_session_report("session_missing")
    assert exc.value.status == 404
