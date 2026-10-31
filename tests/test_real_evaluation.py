from collector import db
from test_cases.real_evaluation import normal_expected, run_real_case


def test_real_evaluation_persists_experiment_and_two_results(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()

    evidence = [
        {
            "device_name": "HQ-R1",
            "observation_id": "obs_1",
            "summary": "接口 up",
        }
    ]

    def fake_diagnose(question, use_kb=True, requested_by="web", **_kwargs):
        return {
            "diagnosis_id": None,
            "conclusion_id": None,
            "answer": {
                "fault_phenomenon": "HQ-R1 网络正常",
                "possible_causes": [],
                "check_steps": [],
                "fix_suggestions": [],
                "recheck_method": "ping 10.10.10.100",
                "missing_information": "无",
            },
            "evidence": evidence,
            "retrieved_docs": [],
            "response_time_ms": 10 if use_kb else 8,
            "total_response_time_ms": 20 if use_kb else 18,
            "error": None,
            "model_status": "success",
            "model_error_code": None,
            "model_error_message": None,
            "knowledge_base": {"status": "disabled"},
        }

    monkeypatch.setattr(
        "test_cases.real_evaluation.diagnose",
        fake_diagnose,
    )

    payload = run_real_case(
        case_external_id="REAL_NORMAL_TEST",
        category="normal",
        question="帮我检查一下总部和分支A之间的网络现在是否正常。",
        expected=normal_expected(),
    )

    assert payload["summary"]["same_evidence_all_cases"] is True
    assert payload["summary"]["by_variant"]["with_kb"]["total"] == 1
    assert payload["summary"]["by_variant"]["without_kb"]["total"] == 1
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM test_cases").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] >= 1
