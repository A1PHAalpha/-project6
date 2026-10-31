#!/usr/bin/env python3
"""Read-only acceptance audit for the NetOps Console.

This script never writes to the application database. It checks the evidence
that is actually present in a SQLite database and the report directory, then
emits a machine-readable JSON summary and a human-readable table.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _check(check_id, requirement, name, value, target, status, detail=""):
    return {
        "id": check_id,
        "requirement": requirement,
        "name": name,
        "value": value,
        "target": target,
        "status": status,
        "detail": detail,
    }


def _status(value, target, comparator=">="):
    if comparator == ">=":
        ok = value >= target
    elif comparator == "==":
        ok = value == target
    else:
        ok = value <= target
    return "PASS" if ok else "FAIL"


def _connect_readonly(db_path: Path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _scalar(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0


def _table_count(conn, table):
    return _as_int(_scalar(conn, f"SELECT COUNT(*) FROM {table}"))


def _latest_run(conn):
    try:
        row = conn.execute(
            """SELECT run_id, summary_json, started_at_utc, published_at_utc
               FROM collection_runs ORDER BY started_at_utc DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return {}
        summary = json.loads(row[1] or "{}")
        return {
            "run_id": row[0],
            "summary": summary,
            "started_at": row[2],
            "published_at": row[3],
        }
    except (sqlite3.Error, ValueError):
        return {}


def _load_articles(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("articles", [])
    except (OSError, ValueError):
        return []


def run_audit(db_path: Path, articles_path: Path, reports_dir: Path) -> dict:
    checks = []
    summary = {
        "db_path": str(db_path),
        "articles_path": str(articles_path),
        "reports_dir": str(reports_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not db_path.exists():
        checks.append(
            _check("DB", "2", "数据库存在", str(db_path), "存在", "FAIL", "数据库文件不存在")
        )
        return {"summary": summary, "checks": checks}

    conn = _connect_readonly(db_path)
    try:
        devices = _table_count(conn, "devices")
        collection_runs = _table_count(conn, "collection_runs")
        observations = _table_count(conn, "state_observations")
        diagnoses = _table_count(conn, "diagnosis_records")
        model_calls = _table_count(conn, "model_calls")
        manual_actions = _table_count(conn, "manual_actions")
        experiments = _table_count(conn, "experiment_runs")
        experiment_results = _table_count(conn, "experiment_results")
        test_cases = _table_count(conn, "test_cases")
        fault_injections = _table_count(conn, "fault_injections")
        change_requests = _table_count(conn, "change_requests")
        terminal_sessions = _table_count(conn, "terminal_sessions")

        router_count = _as_int(
            _scalar(
                conn,
                """SELECT COUNT(*) FROM devices
                   WHERE lower(role) LIKE '%路由器%'
                      OR (lower(model) LIKE '%iosv%' AND lower(model) NOT LIKE '%iosvl2%')""",
            )
        )
        switch_count = _as_int(
            _scalar(
                conn,
                """SELECT COUNT(*) FROM devices
                   WHERE lower(role) LIKE '%交换机%' OR lower(model) LIKE '%iosvl2%'""",
            )
        )
        terminal_count = _as_int(
            _scalar(conn, "SELECT COUNT(*) FROM devices WHERE device_type='linux'")
        )

        summary.update(
            {
                "devices": devices,
                "routers": router_count,
                "switches": switch_count,
                "terminals": terminal_count,
                "collection_runs": collection_runs,
                "observations": observations,
                "diagnoses": diagnoses,
                "model_calls": model_calls,
                "manual_actions": manual_actions,
                "experiments": experiments,
                "experiment_results": experiment_results,
                "test_cases": test_cases,
                "fault_injections": fault_injections,
                "change_requests": change_requests,
                "terminal_sessions": terminal_sessions,
            }
        )

        checks.extend(
            [
                _check("N1-devices", "1", "设备总数", devices, ">= 9", _status(devices, 9)),
                _check("N2-routers", "1", "路由器数量", router_count, ">= 3", _status(router_count, 3)),
                _check("N3-switches", "1", "交换机数量", switch_count, ">= 3", _status(switch_count, 3)),
                _check("N4-terminals", "1", "测试终端数量", terminal_count, ">= 3", _status(terminal_count, 3)),
            ]
        )

        latest = _latest_run(conn)
        run_summary = latest.get("summary", {})
        latest_age_hours = None
        latest_timestamp = latest.get("published_at") or latest.get("started_at")
        if latest_timestamp:
            try:
                parsed = datetime.fromisoformat(
                    str(latest_timestamp).replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                latest_age_hours = (
                    datetime.now(timezone.utc) - parsed
                ).total_seconds() / 3600
            except ValueError:
                latest_age_hours = None
        ssh_success = _as_int(run_summary.get("ssh_commands_success"))
        ssh_failed = _as_int(run_summary.get("ssh_commands_failed"))
        ssh_total = ssh_success + ssh_failed
        ssh_rate = (ssh_success / ssh_total * 100.0) if ssh_total else 0.0
        conn_success = _as_int(run_summary.get("connectivity_successes"))
        conn_total = _as_int(run_summary.get("connectivity_tests"))
        checks.extend(
            [
                _check("C1-runs", "2", "采集批次数量", collection_runs, ">= 1", _status(collection_runs, 1)),
                _check("C2-observations", "2", "状态观测数量", observations, ">= 100", _status(observations, 100)),
                _check("C3-ssh-rate", "2", "最新批次 SSH 成功率", f"{ssh_rate:.1f}%", ">= 95%", _status(ssh_rate, 95)),
                _check(
                    "C4-connectivity",
                    "2",
                    "最新批次连通性",
                    f"{conn_success}/{conn_total}",
                    "记录",
                    "PASS" if conn_total else "WARN",
                ),
                _check(
                    "C5-missing-time",
                    "2",
                    "观测缺失采集时间",
                    _as_int(_scalar(conn, "SELECT COUNT(*) FROM state_observations WHERE observed_at_utc IS NULL OR observed_at_utc=''")),
                    "0",
                    _status(
                        _as_int(_scalar(conn, "SELECT COUNT(*) FROM state_observations WHERE observed_at_utc IS NULL OR observed_at_utc=''")),
                        0,
                        "==",
                    ),
                ),
                _check(
                    "C6-unpublished",
                    "2",
                    "未发布采集批次",
                    _as_int(_scalar(conn, "SELECT COUNT(*) FROM collection_runs WHERE published_at_utc IS NULL OR published_at_utc=''")),
                    "0",
                    _status(
                        _as_int(_scalar(conn, "SELECT COUNT(*) FROM collection_runs WHERE published_at_utc IS NULL OR published_at_utc=''")),
                        0,
                        "==",
                    ),
                ),
                _check(
                    "C7-latest-age",
                    "2",
                    "最新批次时效",
                    (
                        f"{latest_age_hours:.1f} 小时"
                        if latest_age_hours is not None
                        else "无批次"
                    ),
                    "<= 0.5 小时",
                    (
                        "PASS"
                        if latest_age_hours is not None and latest_age_hours <= 0.5
                        else "WARN"
                    ),
                ),
            ]
        )

        articles = _load_articles(articles_path)
        categories = Counter(article.get("category") for article in articles)
        metadata_missing = [
            article.get("id")
            for article in articles
            if not all(article.get(key) for key in ("source", "applicable_device", "version"))
        ]
        source_url_count = sum(1 for article in articles if article.get("source_url"))
        checks.extend(
            [
                _check("K1-articles", "3", "知识文章数量", len(articles), ">= 20", _status(len(articles), 20)),
                _check(
                    "K2-categories",
                    "3",
                    "类别覆盖",
                    ", ".join(f"{key}:{value}" for key, value in sorted(categories.items())),
                    "VLAN/路由/DHCP/ACL/接口故障",
                    "PASS"
                    if all(
                        categories.get(category, 0) >= 1
                        for category in ("VLAN", "路由", "DHCP", "ACL", "接口故障")
                    )
                    else "FAIL",
                ),
                _check(
                    "K3-metadata",
                    "3",
                    "来源/设备/版本缺失",
                    len(metadata_missing),
                    "0",
                    _status(len(metadata_missing), 0, "=="),
                    ", ".join(metadata_missing[:10]),
                ),
                _check(
                    "K4-source-url",
                    "3",
                    "来源 URL 覆盖",
                    f"{source_url_count}/{len(articles)}",
                    "100%",
                    "PASS" if articles and source_url_count == len(articles) else "WARN",
                ),
            ]
        )

        checks.extend(
            [
                _check("L1-model-calls", "4", "真实模型调用", model_calls, ">= 1", _status(model_calls, 1)),
                _check("L2-diagnoses", "4", "诊断记录", diagnoses, ">= 1", _status(diagnoses, 1)),
                _check(
                    "L3-retrieval",
                    "4",
                    "检索查询",
                    _table_count(conn, "retrieval_queries"),
                    ">= 1",
                    _status(_table_count(conn, "retrieval_queries"), 1),
                ),
                _check(
                    "L4-context",
                    "4",
                    "上下文快照",
                    _table_count(conn, "context_snapshots"),
                    ">= 1",
                    _status(_table_count(conn, "context_snapshots"), 1),
                ),
                _check(
                    "L5-prompt",
                    "4",
                    "Prompt 版本",
                    _table_count(conn, "prompt_versions"),
                    ">= 1",
                    _status(_table_count(conn, "prompt_versions"), 1),
                ),
            ]
        )

        checks.extend(
            [
                _check("E1-experiments", "7", "评测实验", experiments, ">= 1", _status(experiments, 1)),
                _check(
                    "E2-results",
                    "7",
                    "评测结果",
                    experiment_results,
                    ">= 20",
                    _status(experiment_results, 20),
                ),
                _check("E3-cases", "7", "测试用例", test_cases, ">= 10", _status(test_cases, 10)),
                _check(
                    "E4-fault-injections",
                    "7",
                    "真实故障注入",
                    fault_injections,
                    ">= 8",
                    _status(fault_injections, 8),
                ),
            ]
        )

        checks.extend(
            [
                _check(
                    "O1-change-requests",
                    "6",
                    "变更单",
                    change_requests,
                    ">= 1（P5 可豁免）",
                    "PASS" if change_requests >= 1 else "WARN",
                ),
                _check("O2-terminal", "6", "审计终端会话", terminal_sessions, ">= 1", _status(terminal_sessions, 1)),
                _check("O3-manual-actions", "6", "人工处理记录", manual_actions, ">= 1", _status(manual_actions, 1)),
            ]
        )

        reports = sorted(reports_dir.glob("diagnosis_*.md")) if reports_dir.exists() else []
        html_reports = sorted(reports_dir.glob("diagnosis_*.html")) if reports_dir.exists() else []
        backups = sorted((reports_dir / "backups").glob("*.cfg")) if (reports_dir / "backups").exists() else []
        checks.extend(
            [
                _check("P1-md-report", "8", "Markdown 报告", len(reports), ">= 1", _status(len(reports), 1)),
                _check("P2-html-report", "8", "HTML 报告", len(html_reports), ">= 1", _status(len(html_reports), 1)),
                _check("P3-backups", "8", "配置备份", len(backups), ">= 1", _status(len(backups), 1)),
            ]
        )

        summary["check_status"] = dict(Counter(item["status"] for item in checks))
        if latest:
            summary["latest_run"] = latest.get("run_id")
    finally:
        conn.close()
    return {"summary": summary, "checks": checks}


def print_report(payload: dict) -> None:
    print("NetOps Console 验收审计")
    print("=" * 78)
    summary = payload["summary"]
    for key in (
        "db_path",
        "devices",
        "collection_runs",
        "observations",
        "diagnoses",
        "model_calls",
        "experiments",
        "experiment_results",
        "fault_injections",
    ):
        if key in summary:
            print(f"{key:20s}: {summary[key]}")
    print("-" * 78)
    for item in payload["checks"]:
        print(f"[{item['status']:4s}] {item['id']:22s} {item['name']}: {item['value']} (目标 {item['target']})")
        if item.get("detail"):
            print(f"       {item['detail']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NetOps Console 只读验收审计")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "ops.db"))
    parser.add_argument(
        "--articles",
        default=str(PROJECT_ROOT / "knowledge_base" / "articles.json"),
    )
    parser.add_argument("--reports", default=str(PROJECT_ROOT / "reports"))
    parser.add_argument("--json-output", default=None)
    args = parser.parse_args(argv)

    payload = run_audit(Path(args.db), Path(args.articles), Path(args.reports))
    print_report(payload)
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n审计 JSON 已写入: {output}")
    return 0 if payload["summary"].get("check_status", {}).get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
