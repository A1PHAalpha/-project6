"""一期数据库迁移入口。

运行前会由 ``db.init_db`` 自动把旧库备份为
``<db>.pre-phase1-<timestamp>.bak``，然后重命名旧表并迁移数据。

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
        ]
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count}")
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collection_records_legacy%'"
        ).fetchall()
        if legacy:
            print("legacy tables:", ", ".join(row[0] for row in legacy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
