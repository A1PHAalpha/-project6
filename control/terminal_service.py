"""Audited interactive terminal sessions.

The model never calls this module. A human opens a terminal after explicitly
acknowledging the backup reminder. Manual terminal input is not restricted to
the read-only collection whitelist, but every command and device response is
persisted before the response is returned to the browser.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from netmiko import ConnectHandler

from collector import db
from control.backup_manager import backup_running_config
from control.terminal_confirmations import WriteConfirmationStore
from control.write_policy import is_write_command
from project_config import load_project_env
from rag_engine.sanitize import sanitize_text

load_project_env()


class TerminalError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class _Connection:
    session_id: str
    device: dict
    connection: Any
    lock: threading.Lock


class TerminalManager:
    """Own in-process SSH channels and serialize writes per terminal session."""

    def __init__(self) -> None:
        self._connections: dict[str, _Connection] = {}
        self._manager_lock = threading.Lock()
        self._confirmations = WriteConfirmationStore(
            ttl_seconds=int(os.getenv("TERMINAL_CONFIRM_TTL_SECONDS", "120"))
        )

    def _connection_params(self, device: dict) -> dict:
        raw = db._json_value(device.get("raw_json")) or {}
        username = os.getenv("DEVICE_USERNAME")
        password = os.getenv("DEVICE_PASSWORD")
        if not username or not password:
            raise TerminalError(
                "DEVICE_CREDENTIALS_MISSING",
                "未配置 DEVICE_USERNAME / DEVICE_PASSWORD，无法打开终端",
            )
        device_type = device.get("device_type") or raw.get("device_type")
        host = device.get("mgmt_ip") or raw.get("mgmt_ip")
        if not device_type or not host:
            raise TerminalError("DEVICE_ENDPOINT_MISSING", "设备缺少类型或管理地址")
        params = {
            "device_type": device_type,
            "host": host,
            "username": username,
            "password": password,
            "port": int(raw.get("ssh_port") or 22),
            "timeout": int(os.getenv("TERMINAL_CONNECT_TIMEOUT", "15")),
            "banner_timeout": int(os.getenv("TERMINAL_BANNER_TIMEOUT", "20")),
            "auth_timeout": int(os.getenv("TERMINAL_AUTH_TIMEOUT", "20")),
        }
        if os.getenv("SSH_LEGACY_ALGOS", "false").lower() in ("1", "true", "yes"):
            params["disabled_algorithms"] = {
                "pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]
            }
        return params

    def open(
        self,
        device_name: str,
        *,
        operator: str,
        backup_acknowledged: bool,
    ) -> dict:
        if not backup_acknowledged:
            raise TerminalError(
                "BACKUP_ACKNOWLEDGEMENT_REQUIRED",
                "打开终端前必须确认已完成或已了解备份要求",
            )
        device = db.get_device(device_name, include_retired=False)
        if not device:
            raise TerminalError("DEVICE_NOT_FOUND", "设备不存在或已退役")
        session = db.create_terminal_session(
            device_name,
            operator=operator,
            backup_acknowledged=True,
        )
        session_id = session["id"]
        try:
            connection = ConnectHandler(**self._connection_params(device))
        except TerminalError:
            db.append_terminal_event(
                session_id,
                event_type="error",
                source="system",
                output_text="终端连接前置条件不满足。",
                exit_code=1,
            )
            db.close_terminal_session(session_id, reason="connection_failed")
            raise
        except Exception as exc:
            message = sanitize_text(f"终端连接失败: {type(exc).__name__}: {exc}")
            db.append_terminal_event(
                session_id,
                event_type="error",
                source="system",
                output_text=message,
                exit_code=1,
            )
            db.close_terminal_session(session_id, reason="connection_failed")
            raise TerminalError("TERMINAL_CONNECT_FAILED", message) from exc
        with self._manager_lock:
            self._connections[session_id] = _Connection(
                session_id=session_id,
                device=device,
                connection=connection,
                lock=threading.Lock(),
            )
        return db.get_terminal_session(session_id) or session

    def request_write_confirmation(self, session_id: str, command: str) -> dict:
        session = db.get_terminal_session(session_id)
        if not session:
            raise TerminalError("TERMINAL_NOT_FOUND", "终端会话不存在")
        if session["status"] != "open":
            raise TerminalError("TERMINAL_CLOSED", "终端会话已关闭")
        if session.get("backupStatus") != "completed":
            raise TerminalError(
                "TERMINAL_BACKUP_REQUIRED",
                "写命令前必须完成实际配置备份",
            )
        device = db.get_device(session["deviceName"], include_retired=False)
        if not device:
            raise TerminalError("DEVICE_NOT_FOUND", "设备不存在或已退役")
        if not is_write_command(device.get("device_type") or "", command):
            raise TerminalError("TERMINAL_NOT_WRITE_COMMAND", "该命令不是写命令")
        return self._confirmations.issue(session_id, command)

    def execute(
        self,
        session_id: str,
        command: str,
        *,
        source: str = "manual",
        confirmed: bool = False,
        confirmation_id: str | None = None,
    ) -> list[dict]:
        command = (command or "").strip()
        if not command:
            raise TerminalError("COMMAND_REQUIRED", "命令不能为空")
        if source not in ("manual", "quick_action"):
            raise TerminalError("INVALID_SOURCE", "命令来源不合法")
        if source == "quick_action" and not confirmed:
            raise TerminalError(
                "QUICK_ACTION_CONFIRMATION_REQUIRED",
                "快速操作必须由人工确认后才能下发",
            )
        with self._manager_lock:
            runtime = self._connections.get(session_id)
        if not runtime:
            raise TerminalError("TERMINAL_NOT_CONNECTED", "终端会话未连接或已关闭")
        session = db.get_terminal_session(session_id)
        if not session or session["status"] != "open":
            raise TerminalError("TERMINAL_CLOSED", "终端会话已关闭")

        device = db.get_device(session["deviceName"], include_retired=False)
        if not device:
            db.append_terminal_event(
                session_id,
                event_type="error",
                source="system",
                command_text=sanitize_text(command),
                output_text="命令被拒绝：设备不存在或已退役",
                exit_code=1,
            )
            raise TerminalError("DEVICE_NOT_FOUND", "设备不存在或已退役")
        write = is_write_command(device.get("device_type") or "", command)
        if write:
            if session.get("backupStatus") != "completed":
                db.append_terminal_event(
                    session_id,
                    event_type="error",
                    source="system",
                    command_text=sanitize_text(command),
                    output_text="写命令被拒绝：未完成实际配置备份",
                    exit_code=1,
                )
                raise TerminalError(
                    "TERMINAL_BACKUP_REQUIRED",
                    "写命令前必须完成实际配置备份",
                )
            if not confirmation_id or not self._confirmations.consume(
                session_id, command, confirmation_id
            ):
                db.append_terminal_event(
                    session_id,
                    event_type="error",
                    source="system",
                    command_text=sanitize_text(command),
                    output_text="写命令被拒绝：确认令牌无效、过期或已使用",
                    exit_code=1,
                )
                raise TerminalError(
                    "TERMINAL_CONFIRMATION_REQUIRED",
                    "写命令需要有效的二次确认",
                )

        # Write intent before sending to the device so even a transport failure
        # leaves a reconstruction trail.
        command_event = db.append_terminal_event(
            session_id,
            event_type="command",
            source=source,
            command_text=sanitize_text(command),
            metadata={
                "confirmed": bool(confirmed or write),
                "confirmation_used": write,
            },
        )
        started = time.monotonic()
        with runtime.lock:
            try:
                timeout = int(os.getenv("TERMINAL_COMMAND_TIMEOUT", "30"))
                output = runtime.connection.send_command_timing(
                    command,
                    read_timeout=timeout,
                    strip_prompt=False,
                    strip_command=False,
                )
                safe_output = sanitize_text(output or "")
                db.append_terminal_event(
                    session_id,
                    event_type="output",
                    source=source,
                    output_text=safe_output,
                    exit_code=0,
                    metadata={"duration_ms": int((time.monotonic() - started) * 1000)},
                )
            except Exception as exc:
                message = sanitize_text(f"命令执行失败: {type(exc).__name__}: {exc}")
                db.append_terminal_event(
                    session_id,
                    event_type="error",
                    source=source,
                    output_text=message,
                    exit_code=1,
                    metadata={"duration_ms": int((time.monotonic() - started) * 1000)},
                )
                raise TerminalError("TERMINAL_COMMAND_FAILED", message) from exc
        updated = db.get_terminal_session(session_id)
        return (updated or {}).get("events", [command_event])[-2:]

    def backup(self, session_id: str) -> dict:
        with self._manager_lock:
            runtime = self._connections.get(session_id)
        if not runtime:
            raise TerminalError("TERMINAL_NOT_CONNECTED", "终端会话未连接或已关闭")
        username = os.getenv("DEVICE_USERNAME")
        password = os.getenv("DEVICE_PASSWORD")
        if not username or not password:
            raise TerminalError(
                "DEVICE_CREDENTIALS_MISSING",
                "未配置设备凭据，无法执行备份",
            )
        try:
            result = backup_running_config(runtime.device, username, password)
        except Exception as exc:
            message = sanitize_text(f"备份失败: {type(exc).__name__}: {exc}")
            db.set_terminal_backup_result(
                session_id,
                status="failed",
                backup_ref=None,
                message=message,
            )
            raise TerminalError("TERMINAL_BACKUP_FAILED", message) from exc
        if result.get("status") != "success":
            message = result.get("reason") or "备份失败"
            db.set_terminal_backup_result(
                session_id,
                status="failed",
                backup_ref=None,
                message=message,
            )
            raise TerminalError("TERMINAL_BACKUP_FAILED", message)
        db.set_terminal_backup_result(
            session_id,
            status="completed",
            backup_ref=result.get("backup_path"),
            message="设备配置备份已完成，可继续人工操作。",
        )
        session = db.get_terminal_session(session_id) or {"id": session_id}
        return {
            **session,
            "backup_ref": result.get("backup_path"),
            "sha256": result.get("sha256"),
        }

    def close(self, session_id: str, *, reason: str = "operator_closed") -> dict:
        self._confirmations.clear_session(session_id)
        with self._manager_lock:
            runtime = self._connections.pop(session_id, None)
        if runtime:
            try:
                runtime.connection.disconnect()
            except Exception:
                pass
        try:
            return db.close_terminal_session(session_id, reason=reason)
        except LookupError as exc:
            raise TerminalError("TERMINAL_NOT_FOUND", "终端会话不存在") from exc

    def shutdown(self) -> None:
        with self._manager_lock:
            runtimes = list(self._connections.values())
            self._connections.clear()
        for runtime in runtimes:
            try:
                runtime.connection.disconnect()
            except Exception:
                pass
            try:
                db.close_terminal_session(
                    runtime.session_id, reason="application_shutdown"
                )
            except Exception:
                pass


terminal_manager = TerminalManager()
