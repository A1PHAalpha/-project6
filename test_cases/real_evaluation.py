"""Persist a real-evidence diagnosis comparison (with/without KB).

Unlike ``compare_kb`` this module never seeds synthetic evidence. It runs the
normal diagnosis pipeline twice against the current published collection and
stores the experiment, case and both variant results in the application DB.
"""

from __future__ import annotations

import json
from typing import Any

from collector import db
from knowledge_base.build_index import COLLECTION_NAME
from rag_engine.diagnosis_engine import diagnose
from rag_engine.llm_client import get_model_id, get_provider_name
from test_cases.evaluation import (
    aggregate_results,
    evidence_hash,
    score_answer,
)


def run_real_case(
    *,
    case_external_id: str,
    category: str,
    question: str,
    expected: dict,
    expected_notes: str = "",
    suite_name: str = "EVE-NG 真实故障评测",
    created_by: str = "real-eval",
    device_name: str | None = None,
    fault_type: str | None = None,
    fault_description: str | None = None,
) -> dict[str, Any]:
    """Run one real case and persist both KB variants.

    The caller must ensure the latest published collection is the evidence to
    evaluate. For fault cases this means: inject fault -> collect -> call this
    function -> repair/recheck.
    """
    db.init_db()
    suite_id = db.upsert_test_suite(
        suite_name,
        description="基于真实采集批次的同一证据有/无知识库诊断对比",
        version="real-1",
        created_by=created_by,
    )
    case_id = db.upsert_test_case(
        suite_id,
        external_id=case_external_id,
        category=category,
        question=question,
        expected=expected,
        fixture={"source": "live_eve", "device_name": device_name},
        expected_notes=expected_notes or None,
    )
    if fault_type:
        device = db.get_device(device_name, include_retired=False) if device_name else None
        db.register_fault_injection(
            case_id,
            fault_type=fault_type,
            injection_mode="live",
            device_id=device["device_id"] if device else None,
            description=fault_description,
            injected_by=created_by,
            status="injected",
        )

    active_index = db.get_active_knowledge_index(COLLECTION_NAME)
    experiment_id = db.start_experiment_run(
        suite_id,
        model_id=get_model_id(),
        provider=get_provider_name(),
        kb_index_version_id=(
            active_index["index_version_id"] if active_index else None
        ),
        created_by=created_by,
    )

    results = []
    summary_rows = []
    same_evidence = True
    try:
        with_kb = diagnose(question, use_kb=True, requested_by=created_by)
        without_kb = diagnose(question, use_kb=False, requested_by=created_by)
        evidence_hash_with = evidence_hash(with_kb.get("evidence") or [])
        evidence_hash_without = evidence_hash(without_kb.get("evidence") or [])
        same_evidence = evidence_hash_with == evidence_hash_without

        for kb_enabled, result in ((True, with_kb), (False, without_kb)):
            score = score_answer(result.get("answer") or {}, expected)
            evaluation = {
                **score,
                "evidence_hash": evidence_hash(result.get("evidence") or []),
                "evidence": result.get("evidence") or [],
                "retrieved_docs": result.get("retrieved_docs") or [],
                "knowledge_base": result.get("knowledge_base"),
                "model_status": result.get("model_status"),
                "model_error_code": result.get("model_error_code"),
                "model_error_message": result.get("model_error_message"),
                "same_evidence": same_evidence,
            }
            db.record_experiment_result(
                experiment_id,
                case_id,
                kb_enabled=kb_enabled,
                correctness=score["correctness"],
                correctness_score=score["correctness_score"],
                executability_score=score["executability_score"],
                diagnosis_id=result.get("diagnosis_id"),
                conclusion_id=result.get("conclusion_id"),
                evidence_hash=evaluation["evidence_hash"],
                llm_response_time_ms=result.get("response_time_ms"),
                total_response_time_ms=result.get("total_response_time_ms"),
                error_code=result.get("model_error_code") or result.get("error"),
                error_message=result.get("model_error_message") or result.get("error"),
                answer=result.get("answer") or {},
                evaluation=evaluation,
            )
            summary_rows.append(
                {
                    "kb_enabled": kb_enabled,
                    "correctness": score["correctness"],
                    "executability_score": score["executability_score"],
                    "total_response_time_ms": result.get("total_response_time_ms"),
                }
            )
            results.append(
                {
                    "kb_enabled": kb_enabled,
                    "diagnosis_id": result.get("diagnosis_id"),
                    "answer": result.get("answer") or {},
                    "score": score,
                    "retrieved_docs": result.get("retrieved_docs") or [],
                    "evidence_hash": evaluation["evidence_hash"],
                }
            )

        summary = aggregate_results(summary_rows)
        summary["same_evidence_all_cases"] = same_evidence
        summary["case_external_id"] = case_external_id
        summary["question"] = question
        summary["category"] = category
        db.finish_experiment_run(experiment_id, status="completed", summary=summary)
        db.record_audit_event(
            actor=created_by,
            action="evaluation.real_case.completed",
            target_type="experiment",
            target_id=experiment_id,
            details=summary,
        )
        return {
            "experiment_id": experiment_id,
            "suite_id": suite_id,
            "case_id": case_id,
            "summary": summary,
            "results": results,
        }
    except Exception as exc:
        db.finish_experiment_run(
            experiment_id,
            status="failed",
            summary={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise


def normal_expected() -> dict:
    return {
        "must_mention": [],
        "must_mention_any": ["正常", "无故障", "未发现异常", "未发现故障"],
        "expected_devices": ["HQ-R1"],
        "forbidden": ["链路中断", "默认路由缺失", "ACL 误拦截", "shutdown"],
        "require_recheck": False,
        "expect_no_change": True,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="运行一个真实证据诊断评测")
    parser.add_argument("--question", default="帮我检查一下总部和分支A之间的网络现在是否正常。")
    parser.add_argument("--case-id", default="REAL_NORMAL_01")
    parser.add_argument("--category", default="normal")
    args = parser.parse_args()
    payload = run_real_case(
        case_external_id=args.case_id,
        category=args.category,
        question=args.question,
        expected=normal_expected(),
        expected_notes="真实全量采集后的正常网络检查",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
