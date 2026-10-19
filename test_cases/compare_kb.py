"""
效果测试任务9：对比脚本。

对 test_cases.scenarios.SCENARIOS 中的每个场景：
1. 用临时隔离的 SQLite 数据库（通过环境变量 SQLITE_DB_PATH 指向临时文件）
   和临时 Chroma 索引，避免污染真实的 data/ops.db / data/chroma。
2. 调用 seed_scenario() 写入模拟采集证据。
3. 分别以 use_kb=True / use_kb=False 调用 diagnosis_engine.diagnose()，
   记录两者的完整结构化回答与响应耗时。
4. 将对比结果写入 JSON 文件（供人工评估），并在控制台打印简要摘要。

注意：当前环境没有真实 .env / LLM_API_KEY，diagnose() 内部对 LLM 的调用会
抛出异常并走 diagnosis_engine.py 里的 except 分支，返回占位结构
(fault_phenomenon="LLM调用失败")。这是预期行为，不是脚本的bug——
一旦在 .env 中配置真实的 LLM_API_KEY，重新运行本脚本即可拿到真实模型输出。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import load_project_env

load_project_env()


def _setup_isolated_env():
    """
    在 import collector.db / knowledge_base.retriever 之前设置隔离的
    SQLITE_DB_PATH 和 CHROMA_DB_PATH，避免写入真实数据目录。
    返回临时目录路径，供调用方在结束后清理。
    """
    tmp_dir = tempfile.mkdtemp(prefix="rag_compare_")
    os.environ["SQLITE_DB_PATH"] = os.path.join(tmp_dir, "ops_test.db")
    os.environ["CHROMA_DB_PATH"] = os.path.join(tmp_dir, "chroma_test")
    return tmp_dir


def _build_kb_index():
    """
    对比测试需要知识库检索可用（use_kb=True 分支才有意义），
    因此在临时 CHROMA_DB_PATH 下重新构建一次索引。
    """
    from knowledge_base.build_index import build_index
    build_index()


def _init_db_and_devices():
    from collector import db
    db.init_db()
    inventory_path = PROJECT_ROOT / "network" / "device_inventory.json"
    db.load_inventory(str(inventory_path))


def _run_one(scenario: dict) -> dict:
    from rag_engine.diagnosis_engine import diagnose
    from test_cases.scenarios import seed_scenario

    seed_scenario(scenario)

    result_with_kb = diagnose(scenario["question"], use_kb=True)
    result_without_kb = diagnose(scenario["question"], use_kb=False)

    return {
        "id": scenario["id"],
        "category": scenario["category"],
        "question": scenario["question"],
        "expected_notes": scenario["expected_notes"],
        "with_kb": {
            "answer": result_with_kb["answer"],
            "retrieved_docs": [
                {"id": d["id"], "title": d["title"], "category": d["category"]}
                for d in result_with_kb["retrieved_docs"]
            ],
            "response_time_ms": result_with_kb["response_time_ms"],
            "error": result_with_kb["error"],
        },
        "without_kb": {
            "answer": result_without_kb["answer"],
            "retrieved_docs": [],
            "response_time_ms": result_without_kb["response_time_ms"],
            "error": result_without_kb["error"],
        },
    }


def run_all() -> list:
    from test_cases.scenarios import SCENARIOS

    _init_db_and_devices()
    results = []
    for scenario in SCENARIOS:
        print(f"[运行场景] {scenario['id']} ({scenario['category']}) ...")
        results.append(_run_one(scenario))
    return results


def print_summary(results: list):
    print("\n" + "=" * 70)
    print("对比摘要（use_kb=True vs use_kb=False）")
    print("=" * 70)
    for r in results:
        with_kb_phenomenon = r["with_kb"]["answer"].get("fault_phenomenon", "")
        without_kb_phenomenon = r["without_kb"]["answer"].get("fault_phenomenon", "")
        print(f"\n场景 {r['id']} [{r['category']}]")
        print(f"  问题: {r['question']}")
        print(f"  有知识库: {with_kb_phenomenon}  (耗时 {r['with_kb']['response_time_ms']} ms, "
              f"检索到 {len(r['with_kb']['retrieved_docs'])} 篇文档)")
        print(f"  无知识库: {without_kb_phenomenon}  (耗时 {r['without_kb']['response_time_ms']} ms)")
        if r["with_kb"]["error"]:
            print(f"  [注意] 本次为LLM调用异常占位结果（预期行为，需配置真实LLM_API_KEY后复测）: {r['with_kb']['error']}")


def main():
    tmp_dir = _setup_isolated_env()
    try:
        _build_kb_index()
        results = run_all()

        output_path = PROJECT_ROOT / "test_cases" / "compare_results.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print_summary(results)
        print(f"\n完整对比结果已写入: {output_path}")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT))
    main()
