"""三期 API 和页面渲染测试。"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from collector import db
from webapp import main as web_main


def test_phase3_api_status_and_empty_experiments(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    assert web_main.api_knowledge_status()["articles"] >= 26
    assert web_main.api_list_evaluations() == []


def test_phase3_pages_render():
    templates = Environment(
        loader=FileSystemLoader(
            str(Path(__file__).resolve().parents[1] / "webapp" / "templates")
        ),
        autoescape=True,
    )
    knowledge = {
        "sources": 1,
        "articles": 1,
        "active_versions": 1,
        "chunks": 1,
        "active_index": None,
        "indexes": [],
    }
    experiment = {
        "experiment_id": "exp_test",
        "suite_name": "测试套件",
        "status": "completed",
        "model_id": "test",
        "provider": "test",
        "kb_index_version_id": None,
        "started_at_utc": "2026-01-01T00:00:00+00:00",
        "summary": {
            "by_variant": {
                "with_kb": {
                    "total": 1,
                    "accuracy": 1.0,
                    "avg_executability": 1.0,
                    "avg_total_response_time_ms": 10,
                    "correct": 1,
                    "partial": 0,
                    "wrong": 0,
                    "error": 0,
                },
                "without_kb": {
                    "total": 1,
                    "accuracy": 0.5,
                    "avg_executability": 0.5,
                    "avg_total_response_time_ms": 5,
                    "correct": 0,
                    "partial": 1,
                    "wrong": 0,
                    "error": 0,
                },
            },
            "comparison": {"accuracy_delta": 0.5, "executability_delta": 0.5},
            "same_evidence_all_cases": True,
        },
        "results": [],
    }
    assert "知识库目录" in templates.get_template("knowledge.html").render(
        knowledge=knowledge
    )
    assert "exp_test" in templates.get_template("evaluation_detail.html").render(
        experiment=experiment, error=None
    )
