"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import sqlite3
from typing import Any

from ._core import _json_text, _json_value, _new_id, _sha256, get_conn, init_db, now_iso
from .assets import _store_blob_conn
from .collection import _resolve_device_conn


def _change_event_conn(
    conn: sqlite3.Connection,
    change_id: str,
    event_type: str,
    actor: str,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: Any = None,
) -> str:
    payload_text = _json_text(payload) if payload is not None else None
    event_id = _new_id("changeevent")
    conn.execute(
        """INSERT INTO change_events
           (event_id, change_id, event_type, actor, from_status, to_status,
            payload_json, payload_hash, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            change_id,
            event_type,
            actor,
            from_status,
            to_status,
            payload_text,
            _sha256(payload_text or f"{change_id}:{event_type}:{actor}"),
            now_iso(),
        ),
    )
    return event_id


def record_change_event(
    change_id: str,
    event_type: str,
    actor: str,
    *,
    payload: Any = None,
    to_status: str | None = None,
) -> str:
    """追加一条变更事件，供业务层的失败或人工备注使用。"""
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        return _change_event_conn(
            conn,
            change_id,
            event_type,
            actor,
            change["status"],
            to_status,
            payload,
        )


def _get_change_row_conn(conn: sqlite3.Connection, change_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM change_requests WHERE change_id=?",
        (change_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"变更请求不存在: {change_id}")
    return row


def _require_verified_backup_conn(conn: sqlite3.Connection, change_id: str) -> None:
    row = conn.execute(
        """SELECT 1 FROM config_backups
           WHERE change_id=? AND backup_type='before' AND verified=1
           LIMIT 1""",
        (change_id,),
    ).fetchone()
    if not row:
        raise ValueError("执行变更前必须先完成并校验一次配置备份")


def create_change_request(
    *,
    device_name: str,
    change_type: str = "software",
    title: str,
    description: str | None = None,
    risk_level: str = "medium",
    operator: str = "web",
    diagnosis_id: int | None = None,
    conclusion_id: str | None = None,
    session_id: str | None = None,
    rollback_plan: str | None = None,
    planned_at_utc: str | None = None,
    status: str = "draft",
) -> dict:
    """创建人工变更请求。变更本身不会自动执行。"""
    init_db()
    if change_type not in ("physical", "software", "mixed"):
        raise ValueError("change_type 必须是 physical、software 或 mixed")
    if risk_level not in ("low", "medium", "high"):
        raise ValueError("risk_level 必须是 low、medium 或 high")
    if status not in (
        "draft",
        "pending_approval",
        "approved",
        "rejected",
        "cancelled",
    ):
        raise ValueError("新建变更的 status 必须是 draft、pending_approval、approved、rejected 或 cancelled")
    if not title.strip():
        raise ValueError("变更标题不能为空")

    with get_conn() as conn:
        device = _resolve_device_conn(conn, device_name)
        if not device:
            raise ValueError(f"设备不存在: {device_name}")
        if diagnosis_id and not conclusion_id:
            diagnosis = conn.execute(
                "SELECT * FROM diagnosis_records WHERE id=?",
                (diagnosis_id,),
            ).fetchone()
            if diagnosis:
                conclusion_id = diagnosis["conclusion_id"]
                session_id = session_id or diagnosis["session_id"]
        if conclusion_id and not session_id:
            conclusion = conn.execute(
                "SELECT * FROM diagnosis_conclusions WHERE conclusion_id=?",
                (conclusion_id,),
            ).fetchone()
            if conclusion:
                session_id = conclusion["session_id"]
        change_id = _new_id("change")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO change_requests
               (change_id, conclusion_id, diagnosis_id, session_id, device_id,
                device_name, change_type, title, description, risk_level, status,
                requested_by, approved_by, planned_at_utc, started_at_utc,
                finished_at_utc, rollback_plan, legacy_action_id,
                created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL,
                       ?, NULL, ?, ?)""",
            (
                change_id,
                conclusion_id,
                diagnosis_id,
                session_id,
                device["device_id"],
                device["device_name"],
                change_type,
                title.strip(),
                description,
                risk_level,
                status,
                operator,
                planned_at_utc,
                rollback_plan,
                timestamp,
                timestamp,
            ),
        )
        _change_event_conn(
            conn,
            change_id,
            "created",
            operator,
            None,
            status,
            {
                "device_name": device["device_name"],
                "change_type": change_type,
                "title": title.strip(),
            },
        )
    return {
        "change_id": change_id,
        "device_id": device["device_id"],
        "device_name": device["device_name"],
        "status": status,
    }


def list_changes(limit: int = 50, status: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                """SELECT c.*, d.site_id, s.name AS site
                   FROM change_requests c
                   LEFT JOIN devices d ON d.device_id=c.device_id
                   LEFT JOIN sites s ON s.site_id=d.site_id
                   WHERE c.status=? ORDER BY c.updated_at_utc DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT c.*, d.site_id, s.name AS site
                   FROM change_requests c
                   LEFT JOIN devices d ON d.device_id=c.device_id
                   LEFT JOIN sites s ON s.site_id=d.site_id
                   ORDER BY c.updated_at_utc DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_change(change_id: str) -> dict | None:
    with get_conn() as conn:
        change = conn.execute(
            """SELECT c.*, d.device_key, d.mgmt_ip, d.device_type,
                      s.name AS site
               FROM change_requests c
               LEFT JOIN devices d ON d.device_id=c.device_id
               LEFT JOIN sites s ON s.site_id=d.site_id
               WHERE c.change_id=?""",
            (change_id,),
        ).fetchone()
        if not change:
            return None
        steps = conn.execute(
            "SELECT * FROM change_steps WHERE change_id=? ORDER BY seq_no",
            (change_id,),
        ).fetchall()
        approvals = conn.execute(
            """SELECT * FROM change_approvals
               WHERE change_id=? ORDER BY created_at_utc""",
            (change_id,),
        ).fetchall()
        events = conn.execute(
            """SELECT * FROM change_events
               WHERE change_id=? ORDER BY created_at_utc, rowid""",
            (change_id,),
        ).fetchall()
        executions = conn.execute(
            """SELECT ce.*, rb.content_text AS stdout_text,
                      reb.content_text AS stderr_text
               FROM command_executions ce
               LEFT JOIN raw_blobs rb ON rb.blob_id=ce.stdout_blob_id
               LEFT JOIN raw_blobs reb ON reb.blob_id=ce.stderr_blob_id
               JOIN change_steps cs ON cs.step_id=ce.step_id
               WHERE cs.change_id=? ORDER BY ce.created_at_utc""",
            (change_id,),
        ).fetchall()
        physical = conn.execute(
            """SELECT pa.*, rb.content_text AS evidence_text
               FROM physical_actions pa
               LEFT JOIN raw_blobs rb ON rb.blob_id=pa.evidence_blob_id
               JOIN change_steps cs ON cs.step_id=pa.step_id
               WHERE cs.change_id=? ORDER BY pa.performed_at_utc""",
            (change_id,),
        ).fetchall()
        backups = conn.execute(
            "SELECT * FROM config_backups WHERE change_id=? ORDER BY captured_at_utc",
            (change_id,),
        ).fetchall()
        rechecks = conn.execute(
            "SELECT * FROM rechecks WHERE change_id=? ORDER BY executed_at_utc",
            (change_id,),
        ).fetchall()
        attachments = conn.execute(
            """SELECT * FROM attachments
               WHERE (owner_type='change' AND owner_id=?)
                  OR (owner_type='step' AND owner_id IN
                      (SELECT step_id FROM change_steps WHERE change_id=?))
                  OR (owner_type='physical_action' AND owner_id IN
                      (SELECT action_id FROM physical_actions pa
                       JOIN change_steps cs ON cs.step_id=pa.step_id
                       WHERE cs.change_id=?))
               ORDER BY created_at_utc""",
            (change_id, change_id, change_id),
        ).fetchall()
        conclusion = None
        if change["conclusion_id"]:
            conclusion_row = conn.execute(
                "SELECT * FROM diagnosis_conclusions WHERE conclusion_id=?",
                (change["conclusion_id"],),
            ).fetchone()
            conclusion = dict(conclusion_row) if conclusion_row else None
    return {
        "change": dict(change),
        "steps": [dict(row) for row in steps],
        "approvals": [dict(row) for row in approvals],
        "events": [{**dict(row), "payload": _json_value(row["payload_json"]) or {}} for row in events],
        "command_executions": [dict(row) for row in executions],
        "physical_actions": [dict(row) for row in physical],
        "config_backups": [dict(row) for row in backups],
        "rechecks": [
            {
                **dict(row),
                "expected": _json_value(row["expected_json"]) or {},
                "actual": _json_value(row["actual_json"]) or {},
            }
            for row in rechecks
        ],
        "attachments": [dict(row) for row in attachments],
        "conclusion": conclusion,
    }


def list_changes_for_diagnosis(diagnosis_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM change_requests
               WHERE diagnosis_id=? ORDER BY created_at_utc DESC""",
            (diagnosis_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_change_step(
    change_id: str,
    *,
    step_type: str,
    title: str,
    source_type: str = "human",
    command_text: str | None = None,
    expected_result: str | None = None,
    requires_approval: bool = False,
    operator: str = "web",
) -> dict:
    init_db()
    if step_type not in ("physical", "software", "verification"):
        raise ValueError("step_type 必须是 physical、software 或 verification")
    if source_type not in ("llm", "human"):
        raise ValueError("source_type 必须是 llm 或 human")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] in ("completed", "cancelled", "rejected", "rolled_back"):
            raise ValueError(f"当前状态 {change['status']} 不允许新增步骤")
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq_no), 0) + 1 FROM change_steps WHERE change_id=?",
            (change_id,),
        ).fetchone()
        seq_no = int(seq_row[0])
        step_id = _new_id("step")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO change_steps
               (step_id, change_id, seq_no, step_type, source_type, title,
                command_text, expected_result, requires_approval, status,
                executed_by, started_at_utc, finished_at_utc, result_summary,
                created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL,
                       NULL, ?, ?)""",
            (
                step_id,
                change_id,
                seq_no,
                step_type,
                source_type,
                title.strip(),
                command_text,
                expected_result,
                1 if requires_approval else 0,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE change_requests SET updated_at_utc=? WHERE change_id=?",
            (timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "step_added",
            operator,
            change["status"],
            change["status"],
            {"step_id": step_id, "step_type": step_type, "source_type": source_type},
        )
    return {"step_id": step_id, "seq_no": seq_no, "status": "pending"}


def approve_change(
    change_id: str,
    *,
    approver: str,
    decision: str = "approved",
    comment: str | None = None,
) -> dict:
    if decision not in ("approved", "rejected", "changes_requested"):
        raise ValueError("decision 必须是 approved、rejected 或 changes_requested")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] in ("completed", "cancelled", "rolled_back"):
            raise ValueError(f"当前状态 {change['status']} 不允许审批")
        approval_id = _new_id("approval")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO change_approvals
               (approval_id, change_id, approver, decision, comment, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (approval_id, change_id, approver, decision, comment, timestamp),
        )
        target_status = {
            "approved": "approved",
            "rejected": "rejected",
            "changes_requested": "pending_approval",
        }[decision]
        conn.execute(
            """UPDATE change_requests
               SET status=?, approved_by=?, updated_at_utc=? WHERE change_id=?""",
            (
                target_status,
                approver if decision == "approved" else change["approved_by"],
                timestamp,
                change_id,
            ),
        )
        _change_event_conn(
            conn,
            change_id,
            "approval_recorded",
            approver,
            change["status"],
            target_status,
            {"decision": decision, "comment": comment, "approval_id": approval_id},
        )
    return {"change_id": change_id, "approval_id": approval_id, "status": target_status}


def start_change(change_id: str, *, operator: str) -> dict:
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以开始执行")
        timestamp = now_iso()
        conn.execute(
            """UPDATE change_requests
               SET status='executing', started_at_utc=COALESCE(started_at_utc, ?),
                   updated_at_utc=? WHERE change_id=?""",
            (timestamp, timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "execution_started",
            operator,
            change["status"],
            "executing",
            None,
        )
    return {"change_id": change_id, "status": "executing", "started_at_utc": timestamp}


def record_command_execution(
    change_id: str,
    step_id: str,
    *,
    command_text: str,
    output: str | None,
    stderr: str | None = None,
    exit_code: int | None = 0,
    operator: str = "web",
    execution_mode: str = "manual",
    result_summary: str | None = None,
    started_at_utc: str | None = None,
    finished_at_utc: str | None = None,
) -> dict:
    if execution_mode not in ("manual", "readonly", "approved_automation"):
        raise ValueError("execution_mode 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录命令执行")
        _require_verified_backup_conn(conn, change_id)
        step = conn.execute(
            "SELECT * FROM change_steps WHERE step_id=? AND change_id=?",
            (step_id, change_id),
        ).fetchone()
        if not step:
            raise ValueError("变更步骤不存在")
        if step["step_type"] == "physical":
            raise ValueError("物理步骤请使用物理操作记录接口")
        timestamp = now_iso()
        start = started_at_utc or timestamp
        finish = finished_at_utc or timestamp
        stdout_blob_id = _store_blob_conn(conn, output, "text/plain")
        stderr_blob_id = _store_blob_conn(conn, stderr, "text/plain")
        execution_id = _new_id("execution")
        conn.execute(
            """INSERT INTO command_executions
               (execution_id, step_id, device_id, command_text, execution_mode,
                executed_by, started_at_utc, finished_at_utc, exit_code,
                stdout_blob_id, stderr_blob_id, result_summary, approval_id,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                execution_id,
                step_id,
                change["device_id"],
                command_text,
                execution_mode,
                operator,
                start,
                finish,
                exit_code,
                stdout_blob_id,
                stderr_blob_id,
                result_summary,
                timestamp,
            ),
        )
        step_status = "success" if exit_code == 0 else "failed"
        conn.execute(
            """UPDATE change_steps
               SET status=?, executed_by=?, started_at_utc=?, finished_at_utc=?,
                   result_summary=?, updated_at_utc=? WHERE step_id=?""",
            (
                step_status,
                operator,
                start,
                finish,
                result_summary,
                timestamp,
                step_id,
            ),
        )
        conn.execute(
            """UPDATE change_requests
               SET status=CASE WHEN status='approved' THEN 'executing' ELSE status END,
                   started_at_utc=COALESCE(started_at_utc, ?), updated_at_utc=?
               WHERE change_id=?""",
            (start, timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "command_executed",
            operator,
            change["status"],
            "executing" if change["status"] == "approved" else change["status"],
            {
                "execution_id": execution_id,
                "step_id": step_id,
                "command_text": command_text,
                "exit_code": exit_code,
            },
        )
    return {
        "execution_id": execution_id,
        "step_id": step_id,
        "step_status": step_status,
        "exit_code": exit_code,
    }


def record_physical_action(
    change_id: str,
    step_id: str,
    *,
    action_type: str,
    description: str,
    operator: str = "web",
    result: str | None = None,
    evidence_text: str | None = None,
    performed_at_utc: str | None = None,
) -> dict:
    if action_type not in ("cable", "power", "console", "module", "other"):
        raise ValueError("action_type 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录物理操作")
        _require_verified_backup_conn(conn, change_id)
        step = conn.execute(
            "SELECT * FROM change_steps WHERE step_id=? AND change_id=?",
            (step_id, change_id),
        ).fetchone()
        if not step:
            raise ValueError("变更步骤不存在")
        if step["step_type"] not in ("physical", "verification"):
            raise ValueError("当前步骤不是物理操作步骤")
        timestamp = now_iso()
        performed = performed_at_utc or timestamp
        evidence_blob_id = _store_blob_conn(conn, evidence_text, "text/plain")
        action_id = _new_id("physical")
        conn.execute(
            """INSERT INTO physical_actions
               (action_id, step_id, device_id, action_type, description,
                performed_by, performed_at_utc, evidence_blob_id, result,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_id,
                step_id,
                change["device_id"],
                action_type,
                description,
                operator,
                performed,
                evidence_blob_id,
                result,
                timestamp,
            ),
        )
        conn.execute(
            """UPDATE change_steps
               SET status='success', executed_by=?, started_at_utc=?,
                   finished_at_utc=?, result_summary=?, updated_at_utc=?
               WHERE step_id=?""",
            (operator, performed, performed, result or description, timestamp, step_id),
        )
        conn.execute(
            """UPDATE change_requests
               SET status=CASE WHEN status='approved' THEN 'executing' ELSE status END,
                   started_at_utc=COALESCE(started_at_utc, ?), updated_at_utc=?
               WHERE change_id=?""",
            (performed, timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "physical_action_recorded",
            operator,
            change["status"],
            "executing" if change["status"] == "approved" else change["status"],
            {
                "action_id": action_id,
                "step_id": step_id,
                "action_type": action_type,
                "description": description,
            },
        )
    return {"action_id": action_id, "step_id": step_id, "status": "success"}


def record_config_backup(
    change_id: str,
    *,
    content: str,
    storage_ref: str | None,
    sha256: str | None,
    operator: str = "web",
    backup_type: str = "before",
    verified: bool = True,
) -> dict:
    if backup_type not in ("before", "after", "rollback"):
        raise ValueError("backup_type 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录配置备份")
        blob_id = _store_blob_conn(conn, content, "text/plain")
        digest = sha256 or _sha256(content)
        backup_id = _new_id("backup")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO config_backups
               (backup_id, change_id, device_id, config_snapshot_id, blob_id,
                sha256, captured_at_utc, backup_type, verified, storage_ref,
                restore_ref, created_at_utc)
               VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                backup_id,
                change_id,
                change["device_id"],
                blob_id,
                digest,
                timestamp,
                backup_type,
                1 if verified else 0,
                storage_ref,
                timestamp,
            ),
        )
        _change_event_conn(
            conn,
            change_id,
            "config_backup_recorded",
            operator,
            change["status"],
            change["status"],
            {
                "backup_id": backup_id,
                "sha256": digest,
                "storage_ref": storage_ref,
                "backup_type": backup_type,
            },
        )
    return {"backup_id": backup_id, "sha256": digest, "storage_ref": storage_ref}


def record_recheck(
    change_id: str,
    *,
    collection_run_id: str | None,
    source_device_id: str | None,
    source_device_name: str | None,
    target: str,
    check_type: str,
    expected: dict | None,
    actual: dict | None,
    pass_fail: str,
    evidence_observation_id: str | None = None,
    operator: str = "web",
    executed_at_utc: str | None = None,
) -> dict:
    if pass_fail not in ("pass", "fail", "inconclusive"):
        raise ValueError("pass_fail 必须是 pass、fail 或 inconclusive")
    if check_type not in ("ping", "interface", "route", "acl", "application"):
        raise ValueError("check_type 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录复测")
        _require_verified_backup_conn(conn, change_id)
        timestamp = executed_at_utc or now_iso()
        recheck_id = _new_id("recheck")
        conn.execute(
            """INSERT INTO rechecks
               (recheck_id, change_id, collection_run_id, source_device_id,
                source_device_name, target, check_type, expected_json,
                actual_json, pass_fail, evidence_observation_id,
                executed_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                recheck_id,
                change_id,
                collection_run_id,
                source_device_id,
                source_device_name,
                target,
                check_type,
                _json_text(expected),
                _json_text(actual),
                pass_fail,
                evidence_observation_id,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE change_requests SET updated_at_utc=? WHERE change_id=?",
            (timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "recheck_recorded",
            operator,
            change["status"],
            change["status"],
            {
                "recheck_id": recheck_id,
                "pass_fail": pass_fail,
                "target": target,
                "collection_run_id": collection_run_id,
            },
        )
    return {"recheck_id": recheck_id, "pass_fail": pass_fail}


def complete_change(
    change_id: str,
    *,
    operator: str,
    outcome: str = "completed",
) -> dict:
    if outcome not in ("completed", "failed", "rolled_back"):
        raise ValueError("outcome 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        timestamp = now_iso()
        conn.execute(
            """UPDATE change_requests
               SET status=?, finished_at_utc=?, updated_at_utc=?
               WHERE change_id=?""",
            (outcome, timestamp, timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "change_finished",
            operator,
            change["status"],
            outcome,
            {"outcome": outcome},
        )
    return {"change_id": change_id, "status": outcome, "finished_at_utc": timestamp}


__all__ = (
    "_change_event_conn",
    "_get_change_row_conn",
    "_require_verified_backup_conn",
    "add_change_step",
    "approve_change",
    "complete_change",
    "create_change_request",
    "get_change",
    "list_changes",
    "list_changes_for_diagnosis",
    "record_change_event",
    "record_command_execution",
    "record_config_backup",
    "record_physical_action",
    "record_recheck",
    "start_change",
)
