"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_config import load_project_env

load_project_env()

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = os.getenv("SQLITE_DB_PATH") or str(PROJECT_ROOT / "data" / "ops.db")

SCHEMA_VERSION = 6


def now_iso() -> str:
    """所有新表统一使用 UTC ISO8601 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_utc_iso(value: str | None) -> str | None:
    if not value:
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _is_after(left: str | None, right: str | None) -> bool:
    left_utc = _to_utc_iso(left)
    right_utc = _to_utc_iso(right)
    if not left_utc:
        return False
    if not right_utc:
        return True
    try:
        return datetime.fromisoformat(left_utc) > datetime.fromisoformat(right_utc)
    except ValueError:
        return left_utc > right_utc


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _stable_id(prefix: str, key: str) -> str:
    return f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, f'netops:{key}').hex}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    """兼容旧数据中的 JSON 字符串和双重 JSON 编码。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        first = json.loads(value)
    except (TypeError, ValueError):
        return value
    if isinstance(first, str):
        try:
            return json.loads(first)
        except (TypeError, ValueError):
            return first
    return first


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _view_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _legacy_tables(conn: sqlite3.Connection, prefix: str) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? ORDER BY name",
        (f"{prefix}%",),
    ).fetchall()
    return [row[0] for row in rows]


def _schema_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_migrations"):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def _needs_legacy_migration(conn: sqlite3.Connection) -> bool:
    if _table_exists(conn, "devices") and "device_id" not in _columns(conn, "devices"):
        return True
    return _table_exists(conn, "collection_records")


def _backup_before_migration(conn: sqlite3.Connection) -> str | None:
    if not _needs_legacy_migration(conn):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{DB_PATH}.pre-phase1-{stamp}.bak"
    if Path(backup_path).exists():
        return backup_path
    dst = sqlite3.connect(backup_path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return backup_path


def _backup_before_upgrade(conn: sqlite3.Connection) -> str | None:
    """在结构升级前创建可恢复备份，避免就地迁移失败损坏现有数据。"""
    if _needs_legacy_migration(conn):
        return _backup_before_migration(conn)
    version = _schema_version(conn)
    if version == 0 or version >= SCHEMA_VERSION:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{DB_PATH}.pre-v{SCHEMA_VERSION}-{stamp}.bak"
    if Path(backup_path).exists():
        return backup_path
    dst = sqlite3.connect(backup_path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return backup_path


def _rename_legacy_tables(conn: sqlite3.Connection) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for table in ("devices", "collection_records"):
        if not _table_exists(conn, table):
            continue
        legacy_name = f"{table}_legacy"
        if _table_exists(conn, legacy_name):
            legacy_name = f"{table}_legacy_{stamp}"
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_name}"')


def init_db() -> None:
    """Initialize or upgrade the configured SQLite database."""
    from .schema import init_db as initialize_schema

    initialize_schema()


__all__ = (
    "DB_PATH",
    "PROJECT_ROOT",
    "SCHEMA_VERSION",
    "_backup_before_migration",
    "_backup_before_upgrade",
    "_columns",
    "_ensure_column",
    "_is_after",
    "_json_text",
    "_json_value",
    "_legacy_tables",
    "_needs_legacy_migration",
    "_new_id",
    "_rename_legacy_tables",
    "_schema_version",
    "_sha256",
    "_stable_id",
    "_table_exists",
    "_to_utc_iso",
    "_view_exists",
    "get_conn",
    "init_db",
    "now_iso",
)
