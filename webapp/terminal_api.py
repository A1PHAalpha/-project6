"""Human-operated, fully audited terminal API."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from collector import db
from control.terminal_service import TerminalError, terminal_manager
from webapp.api.v1 import ApiProblem

router = APIRouter(prefix="/api/v1/terminal", tags=["terminal"])


class TerminalOpenRequest(BaseModel):
    device_name: str
    operator: str = "web"
    backup_acknowledged: bool = False


@router.post("/sessions", status_code=201)
def open_terminal(payload: TerminalOpenRequest):
    try:
        return terminal_manager.open(
            payload.device_name,
            operator=payload.operator,
            backup_acknowledged=payload.backup_acknowledged,
        )
    except TerminalError as exc:
        status = 409 if exc.code == "BACKUP_ACKNOWLEDGEMENT_REQUIRED" else 400
        raise ApiProblem(status, exc.code, str(exc)) from exc


class TerminalCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4000)
    source: str = "manual"
    confirmed: bool = False


@router.post("/sessions/{session_id}/commands")
def execute_terminal_command(session_id: str, payload: TerminalCommandRequest):
    try:
        events = terminal_manager.execute(
            session_id,
            payload.command,
            source=payload.source,
            confirmed=payload.confirmed,
        )
        return {"session_id": session_id, "events": events}
    except TerminalError as exc:
        status = 409 if exc.code == "QUICK_ACTION_CONFIRMATION_REQUIRED" else 400
        raise ApiProblem(status, exc.code, str(exc)) from exc


@router.post("/sessions/{session_id}/backup")
def backup_terminal_device(session_id: str):
    try:
        return terminal_manager.backup(session_id)
    except TerminalError as exc:
        raise ApiProblem(400, exc.code, str(exc)) from exc


@router.post("/sessions/{session_id}/close")
def close_terminal(session_id: str):
    try:
        return terminal_manager.close(session_id)
    except TerminalError as exc:
        raise ApiProblem(404, exc.code, str(exc)) from exc


@router.get("/sessions/{session_id}")
def terminal_session(session_id: str):
    session = db.get_terminal_session(session_id)
    if not session:
        raise ApiProblem(404, "TERMINAL_NOT_FOUND", "终端会话不存在")
    return session


@router.get("/history/{device_name}")
def terminal_history(device_name: str, limit: int = 20):
    return {"items": db.list_terminal_history(device_name, limit=limit)}
