"""评测编排器使用隔离证据库，但将实验元数据写回应用库。"""
import tempfile

from collector import db
from test_cases import compare_kb


def test_evaluation_runner_persists_two_variants_without_touching_evidence(
    tmp_path, monkeypatch
):
    main_db = str(tmp_path / "ops.db")
    monkeypatch.setattr(db, "DB_PATH", main_db)
    db.init_db()

    def fake_diagnose(question, use_kb=True, requested_by="evaluation", **_kwargs):
        answer = {
            "fault_phenomenon": (
                "链路 down 接口 VLAN 20 Gi0/0 trunk allowed vlan 10 "
                "默认路由 0.0.0.0/0 BR-B-R3 BR-A-R2 ACL deny "
                "192.168.20.0 ICMP HQ-R1 BR-B-SW3 缺少 证据 正常 无故障"
            ),
            "possible_causes": [{"cause": "按证据检查", "kb_refs": []}],
            "check_steps": ["按证据检查"],
            "fix_suggestions": [],
            "recheck_method": "ping 10.10.10.100",
            "missing_information": "无",
        }
        return {
            "diagnosis_id": 1 if use_kb else 2,
            "session_id": "session-test",
            "conclusion_id": "conclusion-test",
            "answer": answer,
            "evidence": [
                {
                    "device_name": "HQ-R1",
                    "run_id": "scratch-run",
                    "collected_at": "2026-01-01T00:00:00+00:00",
                    "status": "success",
                    "summary": "证据",
                }
            ],
            "retrieved_docs": [],
            "response_time_ms": 1,
            "total_response_time_ms": 2,
            "error": None,
            "knowledge_base": {"status": "disabled"},
        }

    monkeypatch.setattr(
        "rag_engine.diagnosis_engine.diagnose",
        fake_diagnose,
    )
    scratch = tempfile.mkdtemp(prefix="eval-runner-", dir=str(tmp_path))
    payload = compare_kb.run_all(
        scratch_dir=scratch,
        build_kb=False,
        created_by="pytest",
    )

    assert payload["summary"]["by_variant"]["with_kb"]["total"] == 10
    assert payload["summary"]["same_evidence_all_cases"] is True
    detail = db.get_experiment(payload["experiment_id"])
    assert len(detail["results"]) == 20
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0
