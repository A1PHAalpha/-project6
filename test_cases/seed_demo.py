"""Deterministic live demo data without a real LLM or device."""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def seed_demo(db_path: str | None = None, reset: bool = False) -> dict:
    """Create a seeded collection run and a canned diagnosis session.

    When ``db_path`` is provided, the caller's ``collector.db.DB_PATH`` is
    intentionally left pointing at that database so it can be inspected or
    served by the app. Tests isolate the global with ``monkeypatch``.
    """
    from collector import db
    from test_cases.scenarios import SCENARIOS, seed_scenario

    if db_path:
        db.DB_PATH = db_path
    if reset:
        path = Path(db.DB_PATH)
        if path.exists():
            path.unlink()

    db.init_db()
    if not db.list_devices():
        db.load_inventory(str(PROJECT_ROOT / "network" / "device_inventory.json"))

    articles = json.loads(
        (PROJECT_ROOT / "knowledge_base" / "articles.json").read_text(encoding="utf-8")
    )["articles"]
    catalog = db.sync_knowledge_catalog(articles)
    index_id = db.begin_knowledge_index(
        index_name="ops_knowledge_base",
        collection_name="ops_knowledge_base",
        model_name=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
        dimension=512,
        cache_ref=os.getenv("FASTEMBED_CACHE_PATH"),
        chroma_path=os.getenv("CHROMA_DB_PATH", "./data/chroma"),
        content_hash="demo-seed",
    )
    db.finish_knowledge_index(
        index_id,
        status="ready",
        article_count=len(articles),
        content_hash="demo-seed",
    )
    article = next(item for item in articles if item["id"] == "KB-IF-002")
    article_meta = catalog["KB-IF-002"]
    retrieved_docs = [
        {
            "id": article["id"],
            "title": article["title"],
            "category": article["category"],
            "applicable_device": article["applicable_device"],
            "version": article["version"],
            "source": article["source"],
            "content": article["content"],
            "distance": 0.42,
            "rank": 1,
            "content_hash": article_meta.get("content_hash"),
            "article_version_id": article_meta.get("article_version_id"),
            "index_version_id": index_id,
            "source_url": article_meta.get("source_url"),
            "applicable_platform": article_meta.get("applicable_platform"),
        }
    ]

    scenario = next(item for item in SCENARIOS if item["id"] == "LINK_DOWN_01")
    seed_scenario(scenario)

    evidence = [
        {
            "device_name": "BR-A-R2",
            "run_id": "run_demo",
            "collected_at": "2026-10-23T00:00:00+00:00",
            "status": "success",
            "summary": "Gi0/1 down/down",
        },
        {
            "device_name": "BR-A-SW2",
            "run_id": "run_demo",
            "collected_at": "2026-10-23T00:00:00+00:00",
            "status": "success",
            "summary": "Gi0/0 down/down",
        },
    ]
    saved = db.save_diagnosis_run(
        question=scenario["question"],
        model_id="demo-seed",
        prompt_template_id="diagnosis_v2",
        prompt_version=None,
        llm_params={},
        answer={
            "fault_phenomenon": "BR-A-R2 与 BR-A-SW2 互联链路 down/down",
            "possible_causes": [
                {"cause": "互联链路物理或协议异常", "confidence": "high"}
            ],
            "check_steps": ["show interfaces GigabitEthernet0/1"],
            "fix_suggestions": [
                {
                    "action": "检查互联链路并确认端口状态",
                    "command": "show interfaces GigabitEthernet0/1",
                    "target_device": "BR-A-R2",
                    "requires_manual_confirmation": True,
                }
            ],
            "recheck_method": "ping 192.168.10.100",
            "missing_information": "无",
        },
        evidence=evidence,
        retrieved_docs=retrieved_docs,
        use_kb=True,
        kb_status="success",
        kb_error=None,
        response_time_ms=1,
        total_response_time_ms=1,
        context_messages=[],
        session_id=None,
    )
    return {
        "session_id": saved["session_id"],
        "diagnosis_id": saved["diagnosis_id"],
        "run_id": "run_demo",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="生成可重复的 Live 演示数据")
    parser.add_argument("--db-path", default="./data/demo.db")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    print(seed_demo(args.db_path, reset=args.reset))
