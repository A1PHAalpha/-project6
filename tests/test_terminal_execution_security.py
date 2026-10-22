from types import SimpleNamespace

import pytest

from collector import db
from control import terminal_service
from control.terminal_service import TerminalError, terminal_manager


class FakeConnection:
    def send_command_timing(self, *_args, **_kwargs):
        return "ok"


def _session_for(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DEVICE_USERNAME", "test")
    monkeypatch.setenv("DEVICE_PASSWORD", "test")
    db.init_db()
    db.create_managed_device(
        {
            "name": "LAB-R1",
            "site": "实验室",
            "deviceType": "cisco_ios",
            "managementIp": "192.0.2.30",
        }
    )
    session = db.create_terminal_session(
        "LAB-R1",
        operator="tester",
        backup_acknowledged=True,
    )
    device = db.get_device("LAB-R1")
    terminal_manager._connections[session["id"]] = SimpleNamespace(
        session_id=session["id"],
        device=device,
        connection=FakeConnection(),
        lock=SimpleNamespace(),
    )
    return session, device


def test_retired_device_rejects_command_execution(tmp_path, monkeypatch):
    session, _device = _session_for(tmp_path, monkeypatch)
    try:
        db.archive_devices(["LAB-R1"], actor="test", reason="retired")
        with pytest.raises(TerminalError) as exc:
            terminal_manager.execute(session["id"], "configure terminal")
        assert exc.value.code == "DEVICE_NOT_FOUND"
    finally:
        terminal_manager._connections.pop(session["id"], None)


def test_backup_returns_complete_terminal_session_contract(tmp_path, monkeypatch):
    session, _device = _session_for(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            terminal_service,
            "backup_running_config",
            lambda *_args, **_kwargs: {
                "status": "success",
                "backup_path": "test.cfg",
                "sha256": "abc123",
            },
        )
        result = terminal_manager.backup(session["id"])
        assert result["id"] == session["id"]
        assert result["backupStatus"] == "completed"
        assert result["backupRef"] == "test.cfg"
    finally:
        terminal_manager._connections.pop(session["id"], None)
