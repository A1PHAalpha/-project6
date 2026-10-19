"""安全的数据保留策略执行器。

默认只做 dry-run。只有显式传入 ``--apply`` 才会删除：

- 过期的健康检查记录；
- 没有被任何业务表引用的孤立 raw_blobs。

审计日志策略只统计，不自动删除。所有执行摘要写入 maintenance_runs。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from collector import db


def _cutoff(age_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(
        timespec="seconds"
    )


def _orphan_blob_condition() -> str:
    return """
        NOT EXISTS (SELECT 1 FROM collection_items x WHERE x.raw_blob_id=raw_blobs.blob_id)
        AND NOT EXISTS (SELECT 1 FROM config_snapshots x WHERE x.blob_id=raw_blobs.blob_id)
        AND NOT EXISTS (SELECT 1 FROM config_backups x WHERE x.blob_id=raw_blobs.blob_id)
        AND NOT EXISTS (SELECT 1 FROM tool_call_results x WHERE x.blob_id=raw_blobs.blob_id)
        AND NOT EXISTS (SELECT 1 FROM model_calls x WHERE x.response_blob_id=raw_blobs.blob_id)
        AND NOT EXISTS (SELECT 1 FROM reports x WHERE x.blob_id=raw_blobs.blob_id)
        AND NOT EXISTS (SELECT 1 FROM attachments x WHERE x.blob_id=raw_blobs.blob_id)
        AND NOT EXISTS (SELECT 1 FROM physical_actions x WHERE x.evidence_blob_id=raw_blobs.blob_id)
    """


def _candidate_count(conn, target_table: str, cutoff: str) -> int:
    if target_table == "health_checks":
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM health_checks WHERE checked_at_utc < ?",
                (cutoff,),
            ).fetchone()[0]
        )
    if target_table == "raw_blobs":
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM raw_blobs WHERE raw_blobs.created_at_utc < ? AND {_orphan_blob_condition()}",
                (cutoff,),
            ).fetchone()[0]
        )
    if target_table == "audit_log":
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE created_at_utc < ?",
                (cutoff,),
            ).fetchone()[0]
        )
    return 0


def run_retention(
    *,
    dry_run: bool = True,
    created_by: str = "retention_cli",
) -> dict:
    db.init_db()
    maintenance_id = db.start_maintenance_run(
        "retention",
        created_by=created_by,
    )
    policies = db.list_retention_policies()
    results = []
    try:
        for policy in policies:
            if not policy.get("enabled"):
                results.append(
                    {
                        "policy": policy["name"],
                        "target": policy["target_table"],
                        "status": "disabled",
                        "candidates": 0,
                        "deleted": 0,
                    }
                )
                continue
            cutoff = _cutoff(int(policy["age_days"]))
            target = policy["target_table"]
            with db.get_conn() as conn:
                candidates = _candidate_count(conn, target, cutoff)
                deleted = 0
                if not dry_run and target == "health_checks" and policy["action"] == "delete":
                    deleted = conn.execute(
                        "DELETE FROM health_checks WHERE checked_at_utc < ?",
                        (cutoff,),
                    ).rowcount
                elif (
                    not dry_run
                    and target == "raw_blobs"
                    and policy["action"] == "delete_orphan"
                ):
                    deleted = conn.execute(
                        f"DELETE FROM raw_blobs WHERE raw_blobs.created_at_utc < ? AND {_orphan_blob_condition()}",
                        (cutoff,),
                    ).rowcount
                elif target == "audit_log":
                    deleted = 0
            results.append(
                {
                    "policy": policy["name"],
                    "target": target,
                    "action": policy["action"],
                    "cutoff": cutoff,
                    "status": "applied" if not dry_run else "dry_run",
                    "candidates": candidates,
                    "deleted": deleted,
                }
            )
        summary = {
            "dry_run": dry_run,
            "policies": results,
        }
        db.finish_maintenance_run(
            maintenance_id,
            status="success",
            summary=summary,
        )
        db.record_audit_event(
            actor=created_by,
            action="retention.run",
            target_type="maintenance_run",
            target_id=maintenance_id,
            details=summary,
        )
        return {"maintenance_run_id": maintenance_id, **summary}
    except Exception as exc:
        db.finish_maintenance_run(
            maintenance_id,
            status="failed",
            summary={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="执行数据保留策略（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="实际删除允许清理的数据")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--created-by", default="retention_cli")
    args = parser.parse_args(argv)
    payload = run_retention(
        dry_run=not args.apply,
        created_by=args.created_by,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"maintenance_run_id: {payload['maintenance_run_id']}")
        print(f"dry_run: {payload['dry_run']}")
        for item in payload["policies"]:
            print(
                f"- {item['policy']}: {item['status']} "
                f"候选={item['candidates']} 删除={item['deleted']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
