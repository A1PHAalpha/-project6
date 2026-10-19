"""知识库有无检索的持久化评测入口。

评测在隔离的 scratch SQLite 中写入测试证据和设备诊断，避免清空真实 `ops.db`
的采集记录；实验、用例、结果和汇总写入当前应用数据库，供 Web/API 查询。

用法：
    python -m test_cases.compare_kb
    python -m test_cases.compare_kb --output test_cases/compare_results.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import load_project_env

load_project_env()


def _build_kb_index():
    from knowledge_base.build_index import build_index

    return build_index(rebuild=True)


def _init_main_database(db):
    db.init_db()
    inventory_path = PROJECT_ROOT / "network" / "device_inventory.json"
    db.load_inventory(str(inventory_path))


def _prepare_scratch_db(db, scratch_path: str):
    db.DB_PATH = scratch_path
    db.init_db()
    inventory_path = PROJECT_ROOT / "network" / "device_inventory.json"
    db.load_inventory(str(inventory_path))


def _run_one(db, scenario: dict, scratch_path: str) -> dict:
    from rag_engine.diagnosis_engine import diagnose
    from test_cases.evaluation import evidence_hash
    from test_cases.scenarios import seed_scenario

    old_path = db.DB_PATH
    try:
        db.DB_PATH = scratch_path
        db.reset_collection_data()
        seed_scenario(scenario)
        result_with_kb = diagnose(
            scenario["question"],
            use_kb=True,
            requested_by="evaluation",
        )
        result_without_kb = diagnose(
            scenario["question"],
            use_kb=False,
            requested_by="evaluation",
        )
    finally:
        db.DB_PATH = old_path

    return {
        "id": scenario["id"],
        "category": scenario["category"],
        "question": scenario["question"],
        "expected_notes": scenario["expected_notes"],
        "with_kb": {
            "diagnosis_id": result_with_kb["diagnosis_id"],
            "session_id": result_with_kb["session_id"],
            "answer": result_with_kb["answer"],
            "retrieved_docs": [
                {
                    "id": doc.get("id"),
                    "title": doc.get("title"),
                    "category": doc.get("category"),
                    "version": doc.get("version"),
                    "source": doc.get("source"),
                    "distance": doc.get("distance"),
                    "index_version_id": doc.get("index_version_id"),
                    "article_version_id": doc.get("article_version_id"),
                }
                for doc in result_with_kb["retrieved_docs"]
            ],
            "evidence_hash": evidence_hash(result_with_kb["evidence"]),
            "evidence": result_with_kb["evidence"],
            "response_time_ms": result_with_kb["response_time_ms"],
            "total_response_time_ms": result_with_kb.get("total_response_time_ms"),
            "error": result_with_kb["error"],
            "knowledge_base": result_with_kb["knowledge_base"],
        },
        "without_kb": {
            "diagnosis_id": result_without_kb["diagnosis_id"],
            "session_id": result_without_kb["session_id"],
            "answer": result_without_kb["answer"],
            "retrieved_docs": [],
            "evidence_hash": evidence_hash(result_without_kb["evidence"]),
            "evidence": result_without_kb["evidence"],
            "response_time_ms": result_without_kb["response_time_ms"],
            "total_response_time_ms": result_without_kb.get("total_response_time_ms"),
            "error": result_without_kb["error"],
            "knowledge_base": result_without_kb["knowledge_base"],
        },
    }


def run_all(
    *,
    scratch_dir: str | None = None,
    build_kb: bool = True,
    created_by: str = "compare_kb",
) -> dict:
    """运行全部场景并把评测元数据持久化到当前应用数据库。"""
    from collector import db
    from knowledge_base.retriever import COLLECTION_NAME
    from rag_engine.llm_client import get_model_id, get_provider_name
    from test_cases.evaluation import (
        aggregate_results,
        expected_for_scenario,
        score_answer,
    )
    from test_cases.scenarios import SCENARIOS

    _init_main_database(db)
    main_db_path = db.DB_PATH
    scratch_root = scratch_dir or tempfile.mkdtemp(prefix="netops_eval_")
    scratch_path = os.path.join(scratch_root, "evidence.db")
    try:
        if build_kb:
            _build_kb_index()
        _prepare_scratch_db(db, scratch_path)
        db.DB_PATH = main_db_path

        suite_id = db.upsert_test_suite(
            "企业网络故障诊断对比",
            description="4类故障x2 + 正常 + 证据不足；同一证据对比有/无知识库",
            version="phase3",
            created_by=created_by,
        )
        case_ids: dict[str, str] = {}
        for scenario in SCENARIOS:
            expected = expected_for_scenario(scenario)
            case_id = db.upsert_test_case(
                suite_id,
                external_id=scenario["id"],
                category=scenario["category"],
                question=scenario["question"],
                expected=expected,
                fixture=scenario.get("seed_records") or [],
                expected_notes=scenario.get("expected_notes"),
            )
            case_ids[scenario["id"]] = case_id
            db.register_fault_injection(
                case_id,
                fault_type=scenario["category"],
                injection_mode="synthetic",
                description=scenario.get("expected_notes"),
                injected_by=created_by,
                status="synthetic",
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
        raw_results = []
        output_results = []
        try:
            for scenario in SCENARIOS:
                print(f"[运行场景] {scenario['id']} ({scenario['category']}) ...")
                result = _run_one(db, scenario, scratch_path)
                expected = expected_for_scenario(scenario)
                case_id = case_ids[scenario["id"]]
                scores = {}
                for enabled, key in ((True, "with_kb"), (False, "without_kb")):
                    payload = result[key]
                    score = score_answer(payload["answer"], expected)
                    scores[key] = score
                    db.record_experiment_result(
                        experiment_id,
                        case_id,
                        kb_enabled=enabled,
                        correctness=score["correctness"],
                        correctness_score=score["correctness_score"],
                        executability_score=score["executability_score"],
                        evidence_hash=payload["evidence_hash"],
                        llm_response_time_ms=payload["response_time_ms"],
                        total_response_time_ms=payload["total_response_time_ms"],
                        error_code=(payload.get("error") or "")[:120] or None,
                        error_message=payload.get("error"),
                        answer=payload["answer"],
                        evaluation={
                            **score,
                            "evidence_hash": payload["evidence_hash"],
                            "evidence": payload["evidence"],
                            "retrieved_docs": payload["retrieved_docs"],
                            "knowledge_base": payload.get("knowledge_base"),
                        },
                    )
                    raw_results.append(
                        {
                            "case_external_id": scenario["id"],
                            "category": scenario["category"],
                            "kb_enabled": enabled,
                            "correctness": score["correctness"],
                            "correctness_score": score["correctness_score"],
                            "executability_score": score["executability_score"],
                            "total_response_time_ms": payload["total_response_time_ms"],
                        }
                    )
                same_evidence = (
                    result["with_kb"]["evidence_hash"]
                    == result["without_kb"]["evidence_hash"]
                )
                result["same_evidence"] = same_evidence
                result["expected"] = expected
                result["scores"] = scores
                output_results.append(result)
            summary = aggregate_results(raw_results)
            summary["same_evidence_all_cases"] = all(
                item["same_evidence"] for item in output_results
            )
            db.finish_experiment_run(
                experiment_id,
                status="completed",
                summary=summary,
            )
            db.record_audit_event(
                actor=created_by,
                action="evaluation.completed",
                target_type="experiment",
                target_id=experiment_id,
                details=summary,
            )
        except Exception as exc:
            db.finish_experiment_run(
                experiment_id,
                status="failed",
                summary={"error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        return {
            "experiment_id": experiment_id,
            "suite_id": suite_id,
            "summary": summary,
            "results": output_results,
        }
    finally:
        db.DB_PATH = main_db_path
        if scratch_dir is None:
            shutil.rmtree(scratch_root, ignore_errors=True)


def print_summary(payload: dict):
    summary = payload["summary"]
    print("\n" + "=" * 70)
    print("对比摘要（use_kb=True vs use_kb=False）")
    print("=" * 70)
    print(f"实验 ID: {payload['experiment_id']}")
    for key in ("with_kb", "without_kb"):
        item = summary["by_variant"][key]
        print(
            f"{key}: 准确率={item['accuracy']:.2%}, "
            f"可执行性={item['avg_executability']:.2f}, "
            f"平均全链路耗时={item['avg_total_response_time_ms']} ms, "
            f"正确/部分/错误/异常={item['correct']}/{item['partial']}/"
            f"{item['wrong']}/{item['error']}"
        )
    print(f"同一证据: {summary.get('same_evidence_all_cases')}")
    for result in payload["results"]:
        print(
            f"- {result['id']} [{result['category']}] "
            f"evidence_same={result.get('same_evidence')} "
            f"with_kb={result['scores']['with_kb']['correctness']} "
            f"without_kb={result['scores']['without_kb']['correctness']}"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="知识库有无检索的持久化评测")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "test_cases" / "compare_results.json"),
    )
    parser.add_argument(
        "--scratch-dir",
        help="保留测试证据数据库的目录；默认使用临时目录并在结束后清理",
    )
    parser.add_argument(
        "--skip-kb-build",
        action="store_true",
        help="不重建 Chroma 索引，直接使用当前索引",
    )
    args = parser.parse_args(argv)
    payload = run_all(
        scratch_dir=args.scratch_dir,
        build_kb=not args.skip_kb_build,
        created_by="compare_kb",
    )
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print_summary(payload)
    print(f"\n完整结果已写入: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
