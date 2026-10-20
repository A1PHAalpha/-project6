"""SQLite persistence package for the network operations console."""

from __future__ import annotations

from ._core import _json_text, _new_id, get_conn, init_db, now_iso
from .assets import get_device


def terminal_api_event(row: dict) -> dict:
    return {
        "id": row["event_id"],
        "sequence": row["seq_no"],
        "kind": (
            "command"
            if row["event_type"] == "command"
            else "error"
            if row["event_type"] in ("error", "backup_result") and row.get("exit_code")
            else "system"
            if row["event_type"].startswith("session") or row["event_type"] == "backup_notice"
            else "output"
        ),
        "content": row.get("output_text") or row.get("command_text") or "",
        "createdAt": row["created_at_utc"],
        "eventType": row["event_type"],
        "source": row["source"],
        "exitCode": row.get("exit_code"),
    }


def create_terminal_session(
    device_name: str,
    *,
    operator: str,
    backup_acknowledged: bool,
) -> dict:
    if not backup_acknowledged:
        raise ValueError("BACKUP_ACKNOWLEDGEMENT_REQUIRED")
    device = get_device(device_name, include_retired=False)
    if not device:
        raise LookupError("设备不存在或已退役")
    session_id = _new_id("term")
    timestamp = now_iso()
    init_db()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO terminal_sessions
               (session_id, device_id, device_name, operator, status,
                backup_status, backup_ref, backup_acknowledged_at_utc,
                started_at_utc, last_activity_at_utc, ended_at_utc, close_reason)
               VALUES (?, ?, ?, ?, 'open', 'acknowledged', NULL, ?, ?, ?, NULL, NULL)""",
            (
                session_id,
                device["device_id"],
                device["device_name"],
                operator,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
    append_terminal_event(
        session_id,
        event_type="backup_notice",
        source="system",
        output_text="已确认执行操作前备份。所有命令与设备返回将写入操作记录。",
    )
    append_terminal_event(
        session_id,
        event_type="session_open",
        source="system",
        output_text=f"终端会话已建立：{device['device_name']}，操作人 {operator}。",
    )
    return get_terminal_session(session_id)


def append_terminal_event(
    session_id: str,
    *,
    event_type: str,
    source: str = "manual",
    command_text: str | None = None,
    output_text: str | None = None,
    exit_code: int | None = None,
    metadata: dict | None = None,
) -> dict:
    if event_type not in (
        "session_open",
        "backup_notice",
        "backup_result",
        "command",
        "output",
        "error",
        "session_close",
    ):
        raise ValueError("终端事件类型不合法")
    init_db()
    timestamp = now_iso()
    with get_conn() as conn:
        session = conn.execute("SELECT * FROM terminal_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not session:
            raise LookupError("终端会话不存在")
        next_seq = conn.execute(
            "SELECT COALESCE(MAX(seq_no), 0) + 1 FROM terminal_events WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
        event_id = _new_id("termev")
        conn.execute(
            """INSERT INTO terminal_events
               (event_id, session_id, seq_no, event_type, source, command_text,
                output_text, exit_code, metadata_json, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                session_id,
                next_seq,
                event_type,
                source,
                command_text,
                output_text,
                exit_code,
                _json_text(metadata),
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE terminal_sessions SET last_activity_at_utc=? WHERE session_id=?",
            (timestamp, session_id),
        )
        row = conn.execute("SELECT * FROM terminal_events WHERE event_id=?", (event_id,)).fetchone()
    return dict(row)


def set_terminal_backup_result(
    session_id: str,
    *,
    status: str,
    backup_ref: str | None,
    message: str,
) -> dict:
    if status not in ("completed", "failed"):
        raise ValueError("备份状态不合法")
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT session_id FROM terminal_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            raise LookupError("终端会话不存在")
        conn.execute(
            """UPDATE terminal_sessions
               SET backup_status=?, backup_ref=?, last_activity_at_utc=?
               WHERE session_id=?""",
            (status, backup_ref, now_iso(), session_id),
        )
    append_terminal_event(
        session_id,
        event_type="backup_result",
        source="system",
        output_text=message,
        exit_code=0 if status == "completed" else 1,
        metadata={"backup_ref": backup_ref, "status": status},
    )
    return get_terminal_session(session_id)


def close_terminal_session(
    session_id: str,
    *,
    reason: str = "operator_closed",
) -> dict:
    init_db()
    timestamp = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM terminal_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            raise LookupError("终端会话不存在")
        if row["status"] != "closed":
            conn.execute(
                """UPDATE terminal_sessions
                   SET status='closed', ended_at_utc=?, last_activity_at_utc=?,
                       close_reason=?
                   WHERE session_id=?""",
                (timestamp, timestamp, reason, session_id),
            )
    append_terminal_event(
        session_id,
        event_type="session_close",
        source="system",
        output_text=f"终端会话已关闭：{reason}",
    )
    return get_terminal_session(session_id)


def get_terminal_session(session_id: str) -> dict | None:
    init_db()
    with get_conn() as conn:
        session = conn.execute("SELECT * FROM terminal_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not session:
            return None
        events = conn.execute(
            "SELECT * FROM terminal_events WHERE session_id=? ORDER BY seq_no",
            (session_id,),
        ).fetchall()
    return {
        "id": session["session_id"],
        "deviceName": session["device_name"],
        "operator": session["operator"],
        "status": session["status"],
        "backupStatus": session["backup_status"],
        "backupRef": session["backup_ref"],
        "backupAcknowledgedAt": session["backup_acknowledged_at_utc"],
        "startedAt": session["started_at_utc"],
        "endedAt": session["ended_at_utc"],
        "events": [terminal_api_event(dict(event)) for event in events],
    }


def list_terminal_history(device_name: str, *, limit: int = 20) -> list[dict]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT session_id FROM terminal_sessions
               WHERE device_name=? ORDER BY started_at_utc DESC LIMIT ?""",
            (device_name, max(1, min(limit, 100))),
        ).fetchall()
    return [detail for row in rows if (detail := get_terminal_session(row["session_id"])) is not None]


__all__ = (
    "append_terminal_event",
    "close_terminal_session",
    "create_terminal_session",
    "get_terminal_session",
    "list_terminal_history",
    "set_terminal_backup_result",
    "terminal_api_event",
)
