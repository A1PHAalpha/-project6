"""人工变更页面的模板渲染测试，不依赖异步 HTTP 客户端。"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def _env():
    return Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "webapp" / "templates")),
        autoescape=True,
    )


def test_change_pages_render():
    change = {
        "change_id": "change_test",
        "title": "测试变更",
        "status": "approved",
        "risk_level": "medium",
        "device_name": "HQ-R1",
        "site": "总部",
        "change_type": "mixed",
        "requested_by": "alice",
        "approved_by": "bob",
        "created_at_utc": "2026-10-20T00:00:00+00:00",
        "updated_at_utc": "2026-10-20T00:00:00+00:00",
        "started_at_utc": None,
        "finished_at_utc": None,
        "description": "测试说明",
        "rollback_plan": "回滚说明",
    }
    detail = {
        "change": change,
        "steps": [],
        "approvals": [],
        "events": [],
        "command_executions": [],
        "physical_actions": [],
        "config_backups": [],
        "rechecks": [],
        "attachments": [],
        "conclusion": None,
    }
    env = _env()
    list_html = env.get_template("changes.html").render(changes=[change])
    detail_html = env.get_template("change_detail.html").render(detail=detail, error=None)
    assert "测试变更" in list_html
    assert "change_test" in detail_html
    assert "变更后复测" in detail_html
