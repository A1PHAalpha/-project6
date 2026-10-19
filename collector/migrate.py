"""数据库迁移入口（一期采集模型 + 二期诊断/人工变更模型）。

运行前会由 ``db.init_db`` 自动把旧库备份为
``<db>.pre-v4-<timestamp>.bak``（旧一期库则为 ``pre-phase1``），然后按
``schema_migrations`` 逐步迁移并保留旧表。

用法：
    python -m collector.migrate
"""
from __future__ import annotations

from collector import db


def main() -> int:
    db.init_db()
    with db.get_conn() as conn:
        tables = [
            "sites",
            "devices",
            "interfaces",
            "inventory_versions",
            "inventory_items",
            "collection_runs",
            "device_capture_results",
            "collection_items",
            "state_observations",
            "config_snapshots",
            "topology_snapshots",
            "topology_edges",
            "agent_sessions",
            "agent_messages",
            "context_snapshots",
            "tool_registry",
            "tool_calls",
            "tool_call_results",
            "model_calls",
            "retrieval_queries",
            "retrieval_hits",
            "diagnosis_conclusions",
            "evidence_links",
            "change_requests",
            "change_steps",
            "change_approvals",
            "change_events",
            "command_executions",
            "physical_actions",
            "config_backups",
            "rechecks",
            "attachments",
            "health_checks",
            "audit_log",
            "reports",
        ]
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count}")
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        print(f"schema_version: {version}")
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collection_records_legacy%'"
        ).fetchall()
        if legacy:
            print("legacy tables:", ", ".join(row[0] for row in legacy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
