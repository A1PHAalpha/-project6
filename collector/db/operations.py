"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import json
from typing import Any

from ._core import _json_text, _json_value, _new_id, _stable_id, get_conn, init_db, now_iso


def get_application_setting(key: str, default: Any = None) -> Any:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT setting_json FROM application_settings WHERE setting_key=?",
            (key,),
        ).fetchone()
    return _json_value(row["setting_json"]) if row else default


def set_application_setting(
    key: str,
    value: Any,
    *,
    actor: str = "system",
) -> dict:
    init_db()
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO application_settings
               (setting_key, setting_json, updated_by, updated_at_utc)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(setting_key) DO UPDATE SET
                 setting_json=excluded.setting_json,
                 updated_by=excluded.updated_by,
                 updated_at_utc=excluded.updated_at_utc""",
            (key, _json_text(value), actor, timestamp),
        )
    return {"key": key, "value": value, "updated_by": actor, "updated_at": timestamp}


def create_job(
    job_type: str,
    *,
    request: dict | None = None,
    created_by: str = "system",
) -> dict:
    init_db()
    job_id = _new_id("job")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO background_jobs
               (job_id, job_type, status, progress, message, request_json,
                result_json, error_code, created_by, created_at_utc,
                started_at_utc, finished_at_utc)
               VALUES (?, ?, 'queued', 0, ?, ?, NULL, NULL, ?, ?, NULL, NULL)""",
            (job_id, job_type, "任务已排队", _json_text(request), created_by, timestamp),
        )
    return get_job(job_id)


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    result: Any = None,
    error_code: str | None = None,
) -> dict:
    if status and status not in ("queued", "running", "success", "partial", "failed", "cancelled"):
        raise ValueError("任务状态不合法")
    init_db()
    timestamp = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM background_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise LookupError("任务不存在")
        next_status = status or row["status"]
        terminal = next_status in ("success", "partial", "failed", "cancelled")
        conn.execute(
            """UPDATE background_jobs
               SET status=?, progress=?, message=COALESCE(?, message),
                   result_json=COALESCE(?, result_json),
                   error_code=COALESCE(?, error_code),
                   started_at_utc=CASE
                     WHEN started_at_utc IS NULL AND ? IN ('running','success','partial','failed') THEN ?
                     ELSE started_at_utc END,
                   finished_at_utc=CASE WHEN ? THEN ? ELSE finished_at_utc END
               WHERE job_id=?""",
            (
                next_status,
                max(0, min(progress if progress is not None else row["progress"], 100)),
                message,
                _json_text(result) if result is not None else None,
                error_code,
                next_status,
                timestamp,
                1 if terminal else 0,
                timestamp,
                job_id,
            ),
        )
    return get_job(job_id)


def get_job(job_id: str) -> dict | None:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM background_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["request"] = _json_value(item.pop("request_json")) or {}
    item["result"] = _json_value(item.pop("result_json"))
    return item


def list_knowledge_articles_page(
    *,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    category: str | None = None,
) -> dict:
    init_db()
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    where = ["kav.status='active'"]
    params: list[Any] = []
    if search:
        where.append("(ka.doc_id LIKE ? OR ka.title LIKE ? OR kav.content LIKE ?)")
        needle = f"%{search.strip()}%"
        params.extend([needle, needle, needle])
    if category:
        where.append("ka.category=?")
        params.append(category)
    clause = " AND ".join(where)
    with get_conn() as conn:
        total = conn.execute(
            f"""SELECT COUNT(*) FROM knowledge_articles ka
                JOIN knowledge_article_versions kav
                  ON kav.article_id=ka.article_id
                WHERE {clause}""",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT ka.doc_id, ka.title, ka.category, kav.version_label,
                       kav.applicable_device, kav.content, kav.content_hash,
                       kav.status, ks.title AS source_title, ks.publisher,
                       kav.created_at_utc
                FROM knowledge_articles ka
                JOIN knowledge_article_versions kav
                  ON kav.article_id=ka.article_id
                LEFT JOIN knowledge_sources ks ON ks.source_id=kav.source_id
                WHERE {clause}
                ORDER BY ka.doc_id
                LIMIT ? OFFSET ?""",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        items.append(
            {
                "id": item["doc_id"],
                "title": item["title"],
                "category": item.get("category") or "未分类",
                "applicableDevice": item.get("applicable_device") or "",
                "version": item.get("version_label") or "v1",
                "source": item.get("source_title") or item.get("publisher") or "内部知识库",
                "contentHash": item.get("content_hash") or "",
                "hitCount": 0,
                "lastHitAt": None,
                "summary": (item.get("content") or "")[:160],
                "body": item.get("content") or "",
                "status": item.get("status") or "active",
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def record_health_check(component: str, status: str, details: dict | None = None) -> str:
    if status not in ("ready", "degraded", "error", "unknown"):
        raise ValueError("status 不合法")
    check_id = _new_id("health")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO health_checks
               (check_id, component, status, details_json, checked_at_utc)
               VALUES (?, ?, ?, ?, ?)""",
            (check_id, component, status, _json_text(details), now_iso()),
        )
    return check_id


def list_health_checks(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM health_checks ORDER BY checked_at_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def user_has_permission(username: str, permission_code: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT 1
               FROM users u
               JOIN user_roles ur ON ur.user_id=u.user_id
               JOIN role_permissions rp ON rp.role_id=ur.role_id
               JOIN permissions p ON p.permission_id=rp.permission_id
               WHERE u.username=? AND u.status='active' AND p.permission_code=?
               LIMIT 1""",
            (username, permission_code),
        ).fetchone()
    return row is not None


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.user_id, u.username, u.display_name, u.status,
                      GROUP_CONCAT(r.role_name) AS roles
               FROM users u
               LEFT JOIN user_roles ur ON ur.user_id=u.user_id
               LEFT JOIN roles r ON r.role_id=ur.role_id
               GROUP BY u.user_id
               ORDER BY u.username"""
        ).fetchall()
    return [dict(row) for row in rows]


def record_audit_event(
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    request_id: str | None = None,
    details: dict | None = None,
) -> str:
    audit_id = _new_id("audit")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (audit_id, actor, action, target_type, target_id, request_id,
                details_json, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id,
                actor,
                action,
                target_type,
                target_id,
                request_id,
                _json_text(details),
                now_iso(),
            ),
        )
    return audit_id


def list_audit_log(
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    with get_conn() as conn:
        sql = "SELECT * FROM audit_log"
        clauses = []
        params: list[Any] = []
        if target_type:
            clauses.append("target_type=?")
            params.append(target_type)
        if target_id:
            clauses.append("target_id=?")
            params.append(target_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at_utc DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            **dict(row),
            "details": _json_value(row["details_json"]),
        }
        for row in rows
    ]


def list_retention_policies() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM retention_policies ORDER BY name").fetchall()
    policies = []
    for row in rows:
        item = dict(row)
        item["config"] = _json_value(item.pop("config_json", None))
        policies.append(item)
    return policies


def start_maintenance_run(job_type: str, *, created_by: str = "system") -> str:
    run_id = _new_id("maint")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO maintenance_runs
               (run_id, job_type, status, started_at_utc, finished_at_utc,
                summary_json, created_by, created_at_utc)
               VALUES (?, ?, 'running', ?, NULL, NULL, ?, ?)""",
            (run_id, job_type, timestamp, created_by, timestamp),
        )
    return run_id


def finish_maintenance_run(
    run_id: str,
    *,
    status: str,
    summary: dict | None = None,
) -> dict:
    if status not in ("success", "failed"):
        raise ValueError("status 必须是 success 或 failed")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE maintenance_runs
               SET status=?, finished_at_utc=?, summary_json=?
               WHERE run_id=?""",
            (status, timestamp, _json_text(summary), run_id),
        )
    return {"run_id": run_id, "status": status, "summary": summary}


def upsert_test_suite(
    name: str,
    *,
    description: str | None = None,
    version: str = "1",
    created_by: str = "system",
) -> str:
    suite_id = _stable_id("suite", f"{name}:{version}")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO test_suites
               (suite_id, name, description, version, created_by, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(suite_id) DO UPDATE SET
                 description=excluded.description""",
            (suite_id, name, description, version, created_by, now_iso()),
        )
    return suite_id


def upsert_test_case(
    suite_id: str,
    *,
    external_id: str,
    category: str,
    question: str,
    expected: dict,
    fixture: dict | list | None = None,
    expected_notes: str | None = None,
) -> str:
    case_id = _stable_id("case", f"{suite_id}:{external_id}")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO test_cases
               (case_id, suite_id, external_id, category, question,
                expected_json, fixture_json, expected_notes, active, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(suite_id, external_id) DO UPDATE SET
                 category=excluded.category,
                 question=excluded.question,
                 expected_json=excluded.expected_json,
                 fixture_json=excluded.fixture_json,
                 expected_notes=excluded.expected_notes,
                 active=1""",
            (
                case_id,
                suite_id,
                external_id,
                category,
                question,
                _json_text(expected),
                _json_text(fixture),
                expected_notes,
                now_iso(),
            ),
        )
    return case_id


def register_fault_injection(
    case_id: str,
    *,
    fault_type: str,
    injection_mode: str = "synthetic",
    device_id: str | None = None,
    description: str | None = None,
    injected_by: str = "evaluation",
    status: str = "synthetic",
) -> str:
    if injection_mode not in ("synthetic", "live"):
        raise ValueError("injection_mode 必须是 synthetic 或 live")
    if status not in ("planned", "injected", "verified", "failed", "synthetic"):
        raise ValueError("status 不合法")
    injection_id = _new_id("fault")
    timestamp = now_iso()
    with get_conn() as conn:
        if injection_mode == "synthetic":
            existing = conn.execute(
                """SELECT injection_id FROM fault_injections
                   WHERE case_id=? AND injection_mode='synthetic' AND fault_type=?
                   LIMIT 1""",
                (case_id, fault_type),
            ).fetchone()
            if existing:
                return existing[0]
        conn.execute(
            """INSERT INTO fault_injections
               (injection_id, case_id, device_id, fault_type, injection_mode,
                description, before_blob_id, after_blob_id, injected_at_utc,
                injected_by, verified_at_utc, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?)""",
            (
                injection_id,
                case_id,
                device_id,
                fault_type,
                injection_mode,
                description,
                timestamp if injection_mode == "live" else None,
                injected_by,
                status,
                timestamp,
            ),
        )
    return injection_id


def start_experiment_run(
    suite_id: str,
    *,
    model_id: str,
    provider: str | None = None,
    prompt_version_id: str | None = None,
    kb_index_version_id: str | None = None,
    created_by: str = "evaluation",
) -> str:
    experiment_id = _new_id("experiment")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO experiment_runs
               (experiment_id, suite_id, model_id, provider, prompt_version_id,
                kb_index_version_id, status, started_at_utc, finished_at_utc,
                summary_json, created_by, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'running', ?, NULL, NULL, ?, ?)""",
            (
                experiment_id,
                suite_id,
                model_id,
                provider,
                prompt_version_id,
                kb_index_version_id,
                timestamp,
                created_by,
                timestamp,
            ),
        )
    return experiment_id


def record_experiment_result(
    experiment_id: str,
    case_id: str,
    *,
    kb_enabled: bool,
    correctness: str,
    correctness_score: float,
    executability_score: float,
    diagnosis_id: int | None = None,
    conclusion_id: str | None = None,
    evidence_run_id: str | None = None,
    evidence_hash: str | None = None,
    llm_response_time_ms: int | None = None,
    total_response_time_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    answer: dict | None = None,
    evaluation: dict | None = None,
) -> str:
    if correctness not in ("correct", "partial", "wrong", "error", "skipped"):
        raise ValueError("correctness 不合法")
    result_id = _stable_id("expresult", f"{experiment_id}:{case_id}:{1 if kb_enabled else 0}")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO experiment_results
               (result_id, experiment_id, case_id, kb_enabled, diagnosis_id,
                conclusion_id, evidence_run_id, evidence_hash, correctness,
                correctness_score, executability_score, llm_response_time_ms,
                total_response_time_ms, error_code, error_message,
                answer_json, evaluation_json, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(experiment_id, case_id, kb_enabled) DO UPDATE SET
                 diagnosis_id=excluded.diagnosis_id,
                 conclusion_id=excluded.conclusion_id,
                 evidence_run_id=excluded.evidence_run_id,
                 evidence_hash=excluded.evidence_hash,
                 correctness=excluded.correctness,
                 correctness_score=excluded.correctness_score,
                 executability_score=excluded.executability_score,
                 llm_response_time_ms=excluded.llm_response_time_ms,
                 total_response_time_ms=excluded.total_response_time_ms,
                 error_code=excluded.error_code,
                 error_message=excluded.error_message,
                 answer_json=excluded.answer_json,
                 evaluation_json=excluded.evaluation_json""",
            (
                result_id,
                experiment_id,
                case_id,
                1 if kb_enabled else 0,
                diagnosis_id,
                conclusion_id,
                evidence_run_id,
                evidence_hash,
                correctness,
                correctness_score,
                executability_score,
                llm_response_time_ms,
                total_response_time_ms,
                error_code,
                error_message,
                _json_text(answer or {}),
                _json_text(evaluation or {}),
                now_iso(),
            ),
        )
    return result_id


def finish_experiment_run(
    experiment_id: str,
    *,
    status: str = "completed",
    summary: dict | None = None,
) -> dict:
    if status not in ("completed", "failed", "cancelled"):
        raise ValueError("status 不合法")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE experiment_runs
               SET status=?, finished_at_utc=?, summary_json=?
               WHERE experiment_id=?""",
            (status, timestamp, _json_text(summary), experiment_id),
        )
    return {
        "experiment_id": experiment_id,
        "status": status,
        "finished_at_utc": timestamp,
        "summary": summary,
    }


def list_experiments(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT er.*, ts.name AS suite_name,
                      (SELECT COUNT(*) FROM experiment_results x
                       WHERE x.experiment_id=er.experiment_id) AS result_count
               FROM experiment_runs er
               JOIN test_suites ts ON ts.suite_id=er.suite_id
               ORDER BY er.started_at_utc DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            **dict(row),
            "summary": _json_value(row["summary_json"]),
        }
        for row in rows
    ]


def get_experiment(experiment_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT er.*, ts.name AS suite_name, ts.description AS suite_description
               FROM experiment_runs er
               JOIN test_suites ts ON ts.suite_id=er.suite_id
               WHERE er.experiment_id=?""",
            (experiment_id,),
        ).fetchone()
        if not row:
            return None
        results = conn.execute(
            """SELECT xr.*, tc.external_id, tc.category, tc.question,
                      tc.expected_json, tc.expected_notes
               FROM experiment_results xr
               JOIN test_cases tc ON tc.case_id=xr.case_id
               WHERE xr.experiment_id=?
               ORDER BY tc.external_id, xr.kb_enabled DESC""",
            (experiment_id,),
        ).fetchall()
    result_items = []
    for result in results:
        item = dict(result)
        item["expected"] = _json_value(item.pop("expected_json", None))
        item["answer"] = _json_value(item.pop("answer_json", None))
        item["evaluation"] = _json_value(item.pop("evaluation_json", None))
        result_items.append(item)
    payload = {
        **dict(row),
        "summary": _json_value(row["summary_json"]),
        "results": result_items,
    }
    return payload


def insert_manual_action(diagnosis_id, operator, action_desc, backup_ref, recheck_result, status):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO manual_actions
               (diagnosis_id, created_at, operator, action_desc, backup_ref, recheck_result, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (diagnosis_id, now_iso(), operator, action_desc, backup_ref, recheck_result, status),
        )
        return cur.lastrowid


def list_manual_actions(diagnosis_id=None):
    with get_conn() as conn:
        if diagnosis_id:
            rows = conn.execute(
                "SELECT * FROM manual_actions WHERE diagnosis_id=? ORDER BY id DESC",
                (diagnosis_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM manual_actions ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def set_system_status(status_key: str, status: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO system_status (status_key, status_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(status_key) DO UPDATE SET
                 status_json=excluded.status_json,
                 updated_at=excluded.updated_at""",
            (status_key, json.dumps(status, ensure_ascii=False), now_iso()),
        )


def get_system_status(status_key: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status_json FROM system_status WHERE status_key=?",
            (status_key,),
        ).fetchone()
    return json.loads(row["status_json"]) if row else None


def list_system_status():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status_key, status_json, updated_at FROM system_status ORDER BY status_key"
        ).fetchall()
    return {
        row["status_key"]: {
            "updated_at": row["updated_at"],
            **json.loads(row["status_json"]),
        }
        for row in rows
    }


__all__ = (
    "create_job",
    "finish_experiment_run",
    "finish_maintenance_run",
    "get_application_setting",
    "get_experiment",
    "get_job",
    "get_system_status",
    "insert_manual_action",
    "list_audit_log",
    "list_experiments",
    "list_health_checks",
    "list_knowledge_articles_page",
    "list_manual_actions",
    "list_retention_policies",
    "list_system_status",
    "list_users",
    "record_audit_event",
    "record_experiment_result",
    "record_health_check",
    "register_fault_injection",
    "set_application_setting",
    "set_system_status",
    "start_experiment_run",
    "start_maintenance_run",
    "update_job",
    "upsert_test_case",
    "upsert_test_suite",
    "user_has_permission",
)
