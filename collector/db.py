"""SQLite 数据层：资产、采集批次、状态事实和诊断审计。

一期改造的核心是把“按命令追加日志”升级为：

    collection_runs
      -> device_capture_results
        -> collection_items
          -> state_observations

旧 ``collection_records`` 表会在首次迁移时保留为
``collection_records_legacy``，并创建同名兼容视图，避免现有页面和诊断
代码立即失效。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from project_config import load_project_env

load_project_env()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = os.getenv("SQLITE_DB_PATH") or str(PROJECT_ROOT / "data" / "ops.db")
SCHEMA_VERSION = 2


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY,
    site_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    parent_site_id TEXT REFERENCES sites(site_id),
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    device_key TEXT NOT NULL UNIQUE,
    device_name TEXT NOT NULL UNIQUE,
    site_id TEXT REFERENCES sites(site_id),
    role TEXT,
    vendor TEXT,
    model TEXT,
    os_family TEXT,
    os_version TEXT,
    mgmt_ip TEXT,
    device_type TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'retired', 'unknown')),
    raw_json TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_aliases (
    alias_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (device_id, alias)
);

CREATE TABLE IF NOT EXISTS device_endpoints (
    endpoint_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'snmp', 'ping', 'netconf', 'http')),
    host TEXT NOT NULL,
    port INTEGER,
    credential_ref TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    last_verified_at_utc TEXT,
    UNIQUE (device_id, protocol, host, port)
);

CREATE TABLE IF NOT EXISTS interfaces (
    interface_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    if_name TEXT NOT NULL,
    if_index INTEGER,
    media_type TEXT,
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    UNIQUE (device_id, if_name)
);

CREATE TABLE IF NOT EXISTS credential_refs (
    credential_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    scope TEXT,
    rotated_at_utc TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS inventory_versions (
    version_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    imported_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS inventory_items (
    item_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES inventory_versions(version_id) ON DELETE CASCADE,
    device_key TEXT NOT NULL,
    device_id TEXT REFERENCES devices(device_id),
    attributes_json TEXT NOT NULL,
    UNIQUE (version_id, device_key)
);

CREATE TABLE IF NOT EXISTS command_policies (
    policy_id TEXT PRIMARY KEY,
    device_type TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    command_template TEXT NOT NULL,
    read_only INTEGER NOT NULL DEFAULT 1 CHECK (read_only IN (0, 1)),
    timeout_ms INTEGER NOT NULL DEFAULT 15000,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    version TEXT NOT NULL DEFAULT 'phase1',
    UNIQUE (device_type, operation_key, version)
);

CREATE TABLE IF NOT EXISTS raw_blobs (
    blob_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_text TEXT,
    storage_ref TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL
        CHECK (trigger_type IN (
            'startup', 'manual', 'scheduled', 'llm_tool', 'mcp',
            'recheck', 'synthetic', 'migration'
        )),
    scope_type TEXT NOT NULL
        CHECK (scope_type IN ('batch', 'device', 'tool', 'recheck')),
    scope_json TEXT,
    requested_by TEXT,
    source_ref TEXT,
    inventory_version_id TEXT REFERENCES inventory_versions(version_id),
    tool_version TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    published_at_utc TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'success', 'partial', 'failed', 'skipped', 'cancelled')),
    summary_json TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_capture_results (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    result_type TEXT NOT NULL DEFAULT 'full'
        CHECK (result_type IN ('full', 'recheck', 'single')),
    reachability TEXT NOT NULL DEFAULT 'unknown'
        CHECK (reachability IN ('reachable', 'unreachable', 'unknown')),
    overall_status TEXT NOT NULL DEFAULT 'running'
        CHECK (overall_status IN ('running', 'success', 'partial', 'failed', 'skipped')),
    error_code TEXT,
    error_message TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    published_at_utc TEXT,
    UNIQUE (run_id, device_id, result_type)
);

CREATE TABLE IF NOT EXISTS collection_items (
    item_id TEXT PRIMARY KEY,
    result_id TEXT NOT NULL REFERENCES device_capture_results(result_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    source TEXT NOT NULL
        CHECK (source IN ('ssh', 'snmp', 'ping', 'api', 'mcp', 'manual', 'legacy')),
    capture_type TEXT NOT NULL,
    command_or_tool TEXT,
    policy_id TEXT REFERENCES command_policies(policy_id),
    collection_status TEXT NOT NULL
        CHECK (collection_status IN ('success', 'failed', 'timeout', 'skipped')),
    observation_status TEXT NOT NULL
        CHECK (observation_status IN ('observed', 'unreachable', 'unknown', 'error')),
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    raw_blob_id TEXT REFERENCES raw_blobs(blob_id),
    parsed_json TEXT,
    content_hash TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_observations (
    observation_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES collection_items(item_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    state_type TEXT NOT NULL
        CHECK (state_type IN (
            'reachability', 'system', 'interface', 'route', 'vlan',
            'acl', 'arp', 'neighbor', 'dhcp', 'config', 'log', 'command_output'
        )),
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'high',
    created_at_utc TEXT NOT NULL,
    UNIQUE (item_id, state_type, entity_type, entity_key)
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    config_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    item_id TEXT REFERENCES collection_items(item_id),
    blob_id TEXT NOT NULL REFERENCES raw_blobs(blob_id),
    sha256 TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    is_running INTEGER NOT NULL DEFAULT 1 CHECK (is_running IN (0, 1)),
    baseline_id TEXT,
    UNIQUE (item_id)
);

CREATE TABLE IF NOT EXISTS topology_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES collection_runs(run_id),
    source TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    confidence TEXT
);

CREATE TABLE IF NOT EXISTS topology_nodes (
    node_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES topology_snapshots(snapshot_id) ON DELETE CASCADE,
    device_id TEXT REFERENCES devices(device_id),
    node_key TEXT NOT NULL,
    label TEXT,
    UNIQUE (snapshot_id, node_key)
);

CREATE TABLE IF NOT EXISTS topology_edges (
    edge_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES topology_snapshots(snapshot_id) ON DELETE CASCADE,
    a_node_id TEXT NOT NULL REFERENCES topology_nodes(node_id),
    b_node_id TEXT NOT NULL REFERENCES topology_nodes(node_id),
    a_interface_id TEXT REFERENCES interfaces(interface_id),
    b_interface_id TEXT REFERENCES interfaces(interface_id),
    link_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'up',
    source_observation_id TEXT REFERENCES state_observations(observation_id),
    confidence TEXT,
    UNIQUE (snapshot_id, a_node_id, b_node_id, a_interface_id, b_interface_id)
);

CREATE TABLE IF NOT EXISTS diagnosis_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_template_id TEXT NOT NULL,
    llm_params_json TEXT,
    retrieved_doc_ids_json TEXT,
    evidence_json TEXT,
    answer_json TEXT NOT NULL,
    use_kb INTEGER NOT NULL DEFAULT 1,
    response_time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS manual_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id INTEGER,
    created_at TEXT NOT NULL,
    operator TEXT,
    action_desc TEXT,
    backup_ref TEXT,
    recheck_result TEXT,
    status TEXT,
    FOREIGN KEY (diagnosis_id) REFERENCES diagnosis_records(id)
);

CREATE TABLE IF NOT EXISTS system_status (
    status_key TEXT PRIMARY KEY,
    status_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_site ON devices(site_id);
CREATE INDEX IF NOT EXISTS idx_interfaces_device ON interfaces(device_id, if_name);
CREATE INDEX IF NOT EXISTS idx_inventory_items_version ON inventory_items(version_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON collection_runs(started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_runs_published ON collection_runs(published_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_results_device_published
    ON device_capture_results(device_id, published_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_results_run ON device_capture_results(run_id);
CREATE INDEX IF NOT EXISTS idx_items_result ON collection_items(result_id, started_at_utc);
CREATE INDEX IF NOT EXISTS idx_items_device_time
    ON collection_items(device_id, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_obs_device_type_time
    ON state_observations(device_id, state_type, observed_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_obs_item ON state_observations(item_id);
CREATE INDEX IF NOT EXISTS idx_topology_edges_snapshot ON topology_edges(snapshot_id);
"""

SCHEMA = BASE_SCHEMA


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
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _view_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _legacy_tables(conn: sqlite3.Connection, prefix: str) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? ORDER BY name",
        (f"{prefix}%",),
    ).fetchall()
    return [row[0] for row in rows]


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


def _rename_legacy_tables(conn: sqlite3.Connection) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for table in ("devices", "collection_records"):
        if not _table_exists(conn, table):
            continue
        legacy_name = f"{table}_legacy"
        if _table_exists(conn, legacy_name):
            legacy_name = f"{table}_legacy_{stamp}"
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_name}"')


def _site_code(name: str) -> str:
    known = {"总部": "HQ", "分支A": "BR-A", "分支B": "BR-B"}
    if name in known:
        return known[name]
    return "SITE-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8].upper()


def _ensure_site_conn(conn: sqlite3.Connection, name: str | None) -> str:
    site_name = (name or "未分类").strip() or "未分类"
    row = conn.execute("SELECT site_id FROM sites WHERE name=?", (site_name,)).fetchone()
    if row:
        return row[0]
    site_id = _stable_id("site", site_name)
    code = _site_code(site_name)
    if conn.execute("SELECT 1 FROM sites WHERE site_code=?", (code,)).fetchone():
        code = f"{code}-{hashlib.sha1(site_name.encode()).hexdigest()[:4].upper()}"
    conn.execute(
        """INSERT INTO sites
           (site_id, site_code, name, parent_site_id, timezone, created_at_utc)
           VALUES (?, ?, ?, NULL, 'Asia/Shanghai', ?)""",
        (site_id, code, site_name, now_iso()),
    )
    return site_id


def _os_family(device_type: str | None) -> str:
    value = (device_type or "").lower()
    if value.startswith("cisco"):
        return "cisco_ios"
    if value.startswith("huawei"):
        return "huawei_vrp"
    if value == "linux" or value.startswith("linux"):
        return "linux"
    return "unknown"


def _ensure_device_conn(
    conn: sqlite3.Connection,
    device_name: str,
    fallback: dict | None = None,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM devices WHERE device_name=?", (device_name,)
    ).fetchone()
    if row:
        return row
    fallback = fallback or {}
    site_id = _ensure_site_conn(conn, fallback.get("site") or "未分类")
    device_id = _stable_id("dev", device_name)
    timestamp = now_iso()
    raw = json.dumps(fallback, ensure_ascii=False)
    conn.execute(
        """INSERT INTO devices
           (device_id, device_key, device_name, site_id, role, vendor, model,
            os_family, os_version, mgmt_ip, device_type, lifecycle_status,
            raw_json, created_at_utc, updated_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?)""",
        (
            device_id,
            fallback.get("device_key") or device_name,
            device_name,
            site_id,
            fallback.get("role"),
            fallback.get("vendor"),
            fallback.get("model"),
            _os_family(fallback.get("device_type")),
            fallback.get("version") or fallback.get("model"),
            fallback.get("mgmt_ip"),
            fallback.get("device_type") or "unknown",
            raw,
            timestamp,
            timestamp,
        ),
    )
    return conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()


def _ensure_interface_conn(
    conn: sqlite3.Connection,
    device_id: str,
    if_name: str,
    if_index: int | None = None,
) -> str | None:
    name = (if_name or "").strip()
    if not name:
        return None
    row = conn.execute(
        "SELECT interface_id FROM interfaces WHERE device_id=? AND if_name=?",
        (device_id, name),
    ).fetchone()
    if row:
        return row[0]
    interface_id = _stable_id("if", f"{device_id}:{name}")
    timestamp = now_iso()
    conn.execute(
        """INSERT INTO interfaces
           (interface_id, device_id, if_name, if_index, media_type,
            first_seen_at_utc, last_seen_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (interface_id, device_id, name, if_index, "unknown", timestamp, timestamp),
    )
    return interface_id


def _store_blob_conn(
    conn: sqlite3.Connection,
    content: Any,
    media_type: str = "text/plain",
) -> str | None:
    if content is None:
        return None
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    digest = _sha256(text)
    blob_id = f"blob_{digest}"
    conn.execute(
        """INSERT OR IGNORE INTO raw_blobs
           (blob_id, sha256, media_type, size_bytes, content_text, storage_ref, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (blob_id, digest, media_type, len(text.encode("utf-8")), text, "inline", now_iso()),
    )
    return blob_id


def _insert_device_conn(conn: sqlite3.Connection, device: dict) -> dict:
    device_name = device.get("device_name") or device.get("hostname")
    if not device_name:
        raise ValueError("设备缺少 device_name")
    site_id = _ensure_site_conn(conn, device.get("site"))
    device_key = device.get("device_key") or device_name
    existing = conn.execute(
        "SELECT device_id FROM devices WHERE device_key=?", (device_key,)
    ).fetchone()
    device_id = device.get("device_id") or (
        existing[0] if existing else _stable_id("dev", device_key)
    )
    timestamp = now_iso()
    raw_json = json.dumps(device, ensure_ascii=False)
    conn.execute(
        """INSERT INTO devices
           (device_id, device_key, device_name, site_id, role, vendor, model,
            os_family, os_version, mgmt_ip, device_type, lifecycle_status,
            raw_json, created_at_utc, updated_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(device_key) DO UPDATE SET
             device_name=excluded.device_name,
             site_id=excluded.site_id,
             role=excluded.role,
             vendor=excluded.vendor,
             model=excluded.model,
             os_family=excluded.os_family,
             os_version=excluded.os_version,
             mgmt_ip=excluded.mgmt_ip,
             device_type=excluded.device_type,
             raw_json=excluded.raw_json,
             updated_at_utc=excluded.updated_at_utc""",
        (
            device_id,
            device_key,
            device_name,
            site_id,
            device.get("role"),
            device.get("vendor"),
            device.get("model"),
            _os_family(device.get("device_type")),
            device.get("os_version") or device.get("version"),
            device.get("mgmt_ip"),
            device.get("device_type"),
            device.get("lifecycle_status") or "active",
            raw_json,
            timestamp,
            timestamp,
        ),
    )
    row = conn.execute("SELECT * FROM devices WHERE device_key=?", (device_key,)).fetchone()
    device_id = row["device_id"]
    conn.execute(
        """INSERT OR IGNORE INTO device_aliases
           (alias_id, device_id, alias, source, created_at_utc)
           VALUES (?, ?, ?, 'inventory', ?)""",
        (_stable_id("alias", f"{device_id}:{device_name}"), device_id, device_name, timestamp),
    )
    host = device.get("mgmt_ip")
    if host:
        ssh_port = device.get("ssh_port") or 22
        conn.execute(
            """INSERT OR IGNORE INTO device_endpoints
               (endpoint_id, device_id, protocol, host, port, credential_ref,
                enabled, last_verified_at_utc)
               VALUES (?, ?, 'ssh', ?, ?, NULL, 1, NULL)""",
            (_stable_id("ep", f"{device_id}:ssh:{host}:{ssh_port}"), device_id, host, ssh_port),
        )
        if device.get("snmp_port"):
            conn.execute(
                """INSERT OR IGNORE INTO device_endpoints
                   (endpoint_id, device_id, protocol, host, port, credential_ref,
                    enabled, last_verified_at_utc)
                   VALUES (?, ?, 'snmp', ?, ?, NULL, 1, NULL)""",
                (
                    _stable_id("ep", f"{device_id}:snmp:{host}:{device['snmp_port']}"),
                    device_id,
                    host,
                    device["snmp_port"],
                ),
            )
    for interface in device.get("interfaces", []) or []:
        _ensure_interface_conn(conn, device_id, interface)
    result = dict(row)
    result["site"] = conn.execute(
        "SELECT name FROM sites WHERE site_id=?", (site_id,)
    ).fetchone()[0]
    result["site_id"] = site_id
    for key, value in device.items():
        result.setdefault(key, value)
    return result


def upsert_device(device: dict) -> dict:
    init_db()
    with get_conn() as conn:
        return _insert_device_conn(conn, device)


def _upsert_inventory_version_conn(
    conn: sqlite3.Connection,
    source: str,
    source_hash: str,
    captured_at: str | None = None,
    imported_by: str = "system",
    notes: str | None = None,
) -> str:
    row = conn.execute(
        "SELECT version_id FROM inventory_versions WHERE source_hash=? ORDER BY captured_at_utc DESC LIMIT 1",
        (source_hash,),
    ).fetchone()
    if row:
        return row[0]
    version_id = _new_id("inv")
    conn.execute(
        """INSERT INTO inventory_versions
           (version_id, source, source_hash, captured_at_utc, imported_by, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (version_id, source, source_hash, captured_at or now_iso(), imported_by, notes),
    )
    return version_id


def load_inventory_with_version(json_path: str) -> dict:
    init_db()
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    source_hash = hashlib.sha256(raw).hexdigest()
    devices = data.get("devices", [])
    with get_conn() as conn:
        version_id = _upsert_inventory_version_conn(
            conn, str(json_path), source_hash, imported_by="inventory_loader"
        )
        loaded = []
        for device in devices:
            result = _insert_device_conn(conn, device)
            loaded.append(result)
            conn.execute(
                """INSERT INTO inventory_items
                   (item_id, version_id, device_key, device_id, attributes_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(version_id, device_key) DO UPDATE SET
                     device_id=excluded.device_id,
                     attributes_json=excluded.attributes_json""",
                (
                    _stable_id("invitem", f"{version_id}:{result['device_key']}"),
                    version_id,
                    result["device_key"],
                    result["device_id"],
                    json.dumps(device, ensure_ascii=False),
                ),
            )
    return {"inventory_version_id": version_id, "devices": loaded, "source_hash": source_hash}


def load_inventory(json_path: str) -> list[dict]:
    return load_inventory_with_version(json_path)["devices"]


def list_devices() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT d.*, s.name AS site
               FROM devices d
               LEFT JOIN sites s ON s.site_id=d.site_id
               ORDER BY d.device_name"""
        ).fetchall()
    return [dict(row) for row in rows]


def _insert_run_conn(
    conn: sqlite3.Connection,
    run_id: str,
    trigger_type: str,
    scope_type: str,
    requested_by: str | None,
    source_ref: str | None,
    inventory_version_id: str | None,
    tool_version: str | None,
    scope: dict | None = None,
    started_at: str | None = None,
) -> str:
    timestamp = started_at or now_iso()
    conn.execute(
        """INSERT INTO collection_runs
           (run_id, trigger_type, scope_type, scope_json, requested_by, source_ref,
            inventory_version_id, tool_version, started_at_utc, finished_at_utc,
            published_at_utc, status, summary_json, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'running', NULL, ?)""",
        (
            run_id,
            trigger_type,
            scope_type,
            _json_text(scope),
            requested_by,
            source_ref,
            inventory_version_id,
            tool_version or "phase1",
            timestamp,
            timestamp,
        ),
    )
    return run_id


def start_collection_run(
    trigger_type: str = "manual",
    scope_type: str = "batch",
    requested_by: str | None = "cli",
    source_ref: str | None = None,
    inventory_version_id: str | None = None,
    tool_version: str | None = "phase1",
    scope: dict | None = None,
    started_at: str | None = None,
) -> str:
    init_db()
    run_id = _new_id("run")
    with get_conn() as conn:
        _insert_run_conn(
            conn,
            run_id,
            trigger_type,
            scope_type,
            requested_by,
            source_ref,
            inventory_version_id,
            tool_version,
            scope,
            started_at,
        )
    return run_id


def create_device_capture_result(
    run_id: str,
    device_id: str,
    result_type: str = "full",
    started_at: str | None = None,
) -> str:
    result_id = _new_id("result")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO device_capture_results
               (result_id, run_id, device_id, result_type, reachability,
                overall_status, error_code, error_message, started_at_utc,
                finished_at_utc, published_at_utc)
               VALUES (?, ?, ?, ?, 'unknown', 'running', NULL, NULL, ?, NULL, NULL)""",
            (result_id, run_id, device_id, result_type, started_at or now_iso()),
        )
    return result_id


def _insert_item_conn(
    conn: sqlite3.Connection,
    result_id: str,
    device_id: str,
    source: str,
    capture_type: str,
    command_or_tool: str | None,
    collection_status: str,
    observation_status: str,
    raw_output: Any,
    parsed: Any,
    started_at: str,
    finished_at: str,
    policy_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str:
    from collector.state_parser import observations_from_item

    normalized_parsed = _json_value(parsed)
    blob_id = _store_blob_conn(conn, raw_output, "text/plain")
    content_source = raw_output if raw_output is not None else normalized_parsed
    content_hash = _sha256(
        content_source if isinstance(content_source, str)
        else json.dumps(content_source, ensure_ascii=False, sort_keys=True)
    )
    item_id = _new_id("item")
    conn.execute(
        """INSERT INTO collection_items
           (item_id, result_id, device_id, source, capture_type, command_or_tool,
            policy_id, collection_status, observation_status, started_at_utc,
            finished_at_utc, raw_blob_id, parsed_json, content_hash,
            error_code, error_message, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id,
            result_id,
            device_id,
            source,
            capture_type,
            command_or_tool,
            policy_id,
            collection_status,
            observation_status,
            started_at,
            finished_at,
            blob_id,
            _json_text(normalized_parsed),
            content_hash,
            error_code,
            error_message,
            now_iso(),
        ),
    )

    item_for_parser = {
        "command_or_tool": command_or_tool,
        "command": command_or_tool,
        "capture_type": capture_type,
        "raw_output": raw_output,
        "parsed": normalized_parsed,
        "collection_status": collection_status,
        "observation_status": observation_status,
    }
    if collection_status == "success" or (
        collection_status == "failed" and capture_type == "ping"
    ):
        for observation in observations_from_item(item_for_parser):
            _insert_observation_conn(
                conn,
                item_id=item_id,
                device_id=device_id,
                observed_at=finished_at,
                observation=observation,
            )

    if capture_type == "config" and blob_id:
        digest = _sha256(raw_output or "")
        conn.execute(
            """INSERT OR IGNORE INTO config_snapshots
               (config_id, device_id, item_id, blob_id, sha256, captured_at_utc,
                is_running, baseline_id)
               VALUES (?, ?, ?, ?, ?, ?, 1, NULL)""",
            (_stable_id("cfg", item_id), device_id, item_id, blob_id, digest, finished_at),
        )
    return item_id


def _insert_observation_conn(
    conn: sqlite3.Connection,
    item_id: str,
    device_id: str,
    observed_at: str,
    observation: dict,
) -> str:
    payload = observation.get("payload") or {}
    if observation.get("state_type") == "interface":
        _ensure_interface_conn(
            conn,
            device_id,
            observation.get("entity_key") or payload.get("if_name") or "",
            payload.get("if_index"),
        )
    observation_id = _new_id("obs")
    conn.execute(
        """INSERT OR IGNORE INTO state_observations
           (observation_id, item_id, device_id, state_type, entity_type,
            entity_key, observed_at_utc, payload_json, confidence, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            observation_id,
            item_id,
            device_id,
            observation["state_type"],
            observation.get("entity_type") or "unknown",
            observation.get("entity_key") or "unknown",
            observed_at,
            json.dumps(payload, ensure_ascii=False),
            observation.get("confidence") or "high",
            now_iso(),
        ),
    )
    row = conn.execute(
        """SELECT observation_id FROM state_observations
           WHERE item_id=? AND state_type=? AND entity_type=? AND entity_key=?""",
        (
            item_id,
            observation["state_type"],
            observation.get("entity_type") or "unknown",
            observation.get("entity_key") or "unknown",
        ),
    ).fetchone()
    return row[0] if row else observation_id


def insert_collection_item(
    result_id: str,
    device_id: str,
    source: str,
    capture_type: str | None = None,
    command_or_tool: str | None = None,
    collection_status: str = "success",
    observation_status: str | None = None,
    raw_output: Any = None,
    parsed: Any = None,
    policy_id: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str:
    from collector.state_parser import capture_type_for_command

    capture = capture_type or capture_type_for_command(command_or_tool)
    if observation_status is None:
        if collection_status == "success":
            observation_status = "observed"
        elif capture == "ping":
            observation_status = "unreachable"
        else:
            observation_status = "error"
    start = started_at or now_iso()
    finish = finished_at or start
    with get_conn() as conn:
        return _insert_item_conn(
            conn,
            result_id=result_id,
            device_id=device_id,
            source=source,
            capture_type=capture,
            command_or_tool=command_or_tool,
            collection_status=collection_status,
            observation_status=observation_status,
            raw_output=raw_output,
            parsed=parsed,
            started_at=start,
            finished_at=finish,
            policy_id=policy_id,
            error_code=error_code,
            error_message=error_message,
        )


def finalize_device_capture_result(
    result_id: str,
    reachability: str,
    overall_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    finished_at: str | None = None,
    publish: bool = True,
) -> None:
    finish = finished_at or now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE device_capture_results
               SET reachability=?, overall_status=?, error_code=?,
                   error_message=?, finished_at_utc=?, published_at_utc=?
               WHERE result_id=?""",
            (
                reachability,
                overall_status,
                error_code,
                error_message,
                finish,
                finish if publish else None,
                result_id,
            ),
        )


def finish_collection_run(
    run_id: str,
    status: str,
    summary: dict | None = None,
    publish: bool = True,
    finished_at: str | None = None,
) -> None:
    finish = finished_at or now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE collection_runs
               SET status=?, summary_json=?, finished_at_utc=?, published_at_utc=?
               WHERE run_id=?""",
            (
                status,
                _json_text(summary),
                finish,
                finish if publish else None,
                run_id,
            ),
        )


def _insert_collection_batch_conn(
    conn: sqlite3.Connection,
    records: list[dict],
    trigger_type: str,
    result_type: str,
    requested_by: str,
    inventory_version_id: str | None = None,
    scope_type: str | None = None,
) -> dict:
    run_id = _new_id("run")
    timestamps = [
        _to_utc_iso(r.get("collected_at"))
        for r in records
        if r.get("collected_at")
    ]
    timestamp = min(timestamps) if timestamps else now_iso()
    if scope_type is None:
        scope_type = "batch" if len({r.get("device_name") for r in records}) > 1 else "device"
    _insert_run_conn(
        conn,
        run_id,
        trigger_type,
        scope_type,
        requested_by,
        None,
        inventory_version_id,
        "phase1",
        {"device_names": sorted({r.get("device_name") for r in records if r.get("device_name")})},
        timestamp,
    )
    grouped: dict[str, list[dict]] = {}
    for record in records:
        name = record.get("device_name")
        if not name:
            continue
        grouped.setdefault(name, []).append(record)

    item_ids: list[str] = []
    success_count = 0
    failure_count = 0
    for name, device_records in grouped.items():
        device = _ensure_device_conn(conn, name, device_records[0])
        result_id = _new_id("result")
        started = min(
            (_to_utc_iso(r.get("collected_at")) or timestamp)
            for r in device_records
        )
        conn.execute(
            """INSERT INTO device_capture_results
               (result_id, run_id, device_id, result_type, reachability,
                overall_status, started_at_utc)
               VALUES (?, ?, ?, ?, 'unknown', 'running', ?)""",
            (result_id, run_id, device["device_id"], result_type, started),
        )
        statuses = []
        device_success = 0
        for record in device_records:
            status = "success" if record.get("status") == "success" else "failed"
            command = record.get("command")
            from collector.state_parser import capture_type_for_command

            capture = record.get("capture_type") or capture_type_for_command(command)
            observation_status = record.get("observation_status")
            # 旧数据和人工复测里，ping 返回“不可达”常被记成 failed；
            # 只要拿到了原始输出，就说明采集本身成功，失败的是目标可达性。
            if (
                capture == "ping"
                and status == "failed"
                and record.get("raw_output")
            ):
                status = "success"
                observation_status = "observed"
            if observation_status is None:
                observation_status = (
                    "observed" if status == "success"
                    else "unreachable" if capture == "ping"
                    else "error"
                )
            source = record.get("source") or (
                "snmp" if (command or "").upper().startswith("SNMP:")
                else "ping" if (command or "").lower().startswith("ping")
                else "ssh"
            )
            item_id = _insert_item_conn(
                conn,
                result_id=result_id,
                device_id=device["device_id"],
                source=source,
                capture_type=capture,
                command_or_tool=command,
                collection_status=status,
                observation_status=observation_status,
                raw_output=record.get("raw_output"),
                parsed=record.get("parsed"),
                started_at=_to_utc_iso(record.get("collected_at")) or started,
                finished_at=_to_utc_iso(record.get("collected_at")) or started,
                error_code=record.get("error_code"),
                error_message=record.get("fail_reason"),
            )
            item_ids.append(item_id)
            statuses.append(status)
            if status == "success":
                success_count += 1
                device_success += 1
            else:
                failure_count += 1
        reachable = (
            "reachable" if device_success
            else "unreachable" if statuses
            else "unknown"
        )
        overall = "success" if all(s == "success" for s in statuses) else (
            "partial" if any(s == "success" for s in statuses) else "failed"
        )
        finish = max(
            (_to_utc_iso(r.get("collected_at")) or started)
            for r in device_records
        )
        error_message = next(
            (record.get("fail_reason") for record in device_records if record.get("fail_reason")),
            None,
        )
        conn.execute(
            """UPDATE device_capture_results
               SET reachability=?, overall_status=?, error_code=?, error_message=?,
                   finished_at_utc=?, published_at_utc=?
               WHERE result_id=?""",
            (
                reachable,
                overall,
                "collection_failed" if overall == "failed" else None,
                error_message,
                finish,
                finish,
                result_id,
            ),
        )
    run_status = (
        "success" if failure_count == 0
        else "partial" if success_count else "failed"
    )
    summary = {
        "mode": "batch",
        "device_count": len(grouped),
        "success_items": success_count,
        "failed_items": failure_count,
    }
    finish = max(timestamps) if timestamps else now_iso()
    conn.execute(
        """UPDATE collection_runs
           SET status=?, summary_json=?, finished_at_utc=?, published_at_utc=?
           WHERE run_id=?""",
        (run_status, _json_text(summary), finish, finish, run_id),
    )
    _build_topology_conn(conn, run_id)
    return {"run_id": run_id, "item_ids": item_ids, "summary": summary}


def insert_collection_batch(
    records: Iterable[dict],
    trigger_type: str = "synthetic",
    result_type: str = "full",
    requested_by: str = "legacy_api",
    inventory_version_id: str | None = None,
) -> dict:
    init_db()
    records = [dict(record) for record in records]
    if not records:
        return {"run_id": None, "item_ids": [], "summary": {}}
    with get_conn() as conn:
        return _insert_collection_batch_conn(
            conn,
            records,
            trigger_type,
            result_type,
            requested_by,
            inventory_version_id,
        )


def reset_collection_data() -> None:
    """清空采集证据，仅供隔离测试/演示环境使用。

    资产台账、诊断历史和人工处理记录不会被删除。
    """
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM topology_snapshots")
        conn.execute("DELETE FROM config_snapshots")
        conn.execute("DELETE FROM collection_runs")


def insert_collection_record(
    device_name,
    status,
    command=None,
    raw_output=None,
    parsed=None,
    fail_reason=None,
    collected_at=None,
    source=None,
    trigger_type="legacy",
    result_type="full",
):
    """兼容旧调用：一条记录进入独立的采集批次。

    批量场景应优先使用 ``insert_collection_batch``，避免同一设备的多条
    命令被拆成多个批次。
    """
    record = {
        "device_name": device_name,
        "status": status,
        "command": command,
        "raw_output": raw_output,
        "parsed": parsed,
        "fail_reason": fail_reason,
        "collected_at": collected_at,
        "source": source,
    }
    batch = insert_collection_batch(
        [record],
        trigger_type=trigger_type,
        result_type=result_type,
        requested_by="legacy_api",
    )
    return batch["item_ids"][0] if batch["item_ids"] else batch["run_id"]


def _latest_result_conn(
    conn: sqlite3.Connection,
    device_id: str,
    result_types: tuple[str, ...],
) -> sqlite3.Row | None:
    placeholders = ",".join("?" for _ in result_types)
    return conn.execute(
        f"""SELECT dcr.*, r.run_id, r.published_at_utc AS run_published_at_utc
            FROM device_capture_results dcr
            JOIN collection_runs r ON r.run_id=dcr.run_id
            WHERE dcr.device_id=?
              AND dcr.result_type IN ({placeholders})
              AND dcr.published_at_utc IS NOT NULL
              AND r.published_at_utc IS NOT NULL
            ORDER BY julianday(r.published_at_utc) DESC,
                     julianday(dcr.published_at_utc) DESC,
                     dcr.result_id DESC
            LIMIT 1""",
        (device_id, *result_types),
    ).fetchone()


def _result_observations_conn(
    conn: sqlite3.Connection,
    result_id: str,
) -> list[dict]:
    rows = conn.execute(
        """SELECT so.*, ci.command_or_tool, ci.source, ci.capture_type,
                  d.device_name, s.name AS site
           FROM state_observations so
           JOIN collection_items ci ON ci.item_id=so.item_id
           JOIN devices d ON d.device_id=so.device_id
           LEFT JOIN sites s ON s.site_id=d.site_id
           WHERE ci.result_id=?
           ORDER BY so.observed_at_utc DESC, so.rowid DESC""",
        (result_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = _json_value(item.pop("payload_json")) or {}
        result.append(item)
    return result


def _dedupe_observations(observations: list[dict]) -> list[dict]:
    seen: dict[tuple, int] = {}
    deduped = []
    for observation in observations:
        key = (
            observation.get("state_type"),
            observation.get("entity_type"),
            observation.get("entity_key"),
        )
        if key in seen:
            existing = deduped[seen[key]]
            existing_payload = existing.setdefault("payload", {})
            for field, value in (observation.get("payload") or {}).items():
                if existing_payload.get(field) is None and value is not None:
                    existing_payload[field] = value
            continue
        seen[key] = len(deduped)
        deduped.append(observation)
    return deduped


def get_current_device_bundle(device_name: str) -> dict:
    with get_conn() as conn:
        device = conn.execute(
            """SELECT d.*, s.name AS site
               FROM devices d LEFT JOIN sites s ON s.site_id=d.site_id
               WHERE d.device_name=?""",
            (device_name,),
        ).fetchone()
        if not device:
            return {"device": None, "result": None, "items": [], "observations": []}
        result = _latest_result_conn(conn, device["device_id"], ("full",))
        if result is None:
            result = _latest_result_conn(conn, device["device_id"], ("single",))
        if result is None:
            result = _latest_result_conn(
                conn, device["device_id"], ("full", "single", "recheck")
            )
        items = []
        observations = []
        if result:
            item_rows = conn.execute(
                """SELECT ci.*, rb.content_text AS raw_output
                   FROM collection_items ci
                   LEFT JOIN raw_blobs rb ON rb.blob_id=ci.raw_blob_id
                   WHERE ci.result_id=?
                   ORDER BY ci.started_at_utc, ci.item_id""",
                (result["result_id"],),
            ).fetchall()
            items = [dict(row) for row in item_rows]
            observations = _result_observations_conn(conn, result["result_id"])

        single = _latest_result_conn(conn, device["device_id"], ("single",))
        if single and (result is None or single["result_id"] != result["result_id"]):
            observations = _result_observations_conn(conn, single["result_id"]) + observations

        recheck = _latest_result_conn(conn, device["device_id"], ("recheck",))
        recheck_newer = bool(
            recheck
            and (result is None or _is_after(
                recheck["published_at_utc"], result["published_at_utc"]
            ))
        )
        if recheck and (result is None or recheck["result_id"] != result["result_id"]):
            recheck_obs = _result_observations_conn(conn, recheck["result_id"])
            observations = recheck_obs + observations
        effective_reachability = result["reachability"] if result else "unknown"
        if recheck_newer and recheck:
            effective_reachability = recheck["reachability"]

        if result and (
            result["reachability"] == "unreachable"
            or (result["reachability"] == "unknown" and result["overall_status"] == "failed")
        ) and result["result_type"] != "recheck" and not (
            recheck_newer and recheck["reachability"] == "reachable"
        ):
            observations = [
                {
                    "observation_id": None,
                    "item_id": None,
                    "device_id": device["device_id"],
                    "device_name": device["device_name"],
                    "site": device["site"],
                    "state_type": "reachability",
                    "entity_type": "device",
                    "entity_key": device["device_name"],
                    "observed_at_utc": result["finished_at_utc"] or result["started_at_utc"],
                    "payload": {
                        "reachable": False,
                        "error": result["error_message"],
                    },
                    "confidence": "high",
                    "source": "collector",
                    "capture_type": "reachability",
                    "command_or_tool": "device_reachability",
                    "run_id": result["run_id"],
                    "result_id": result["result_id"],
                }
            ]
        observations = _dedupe_observations(observations)
        return {
            "device": dict(device),
            "result": dict(result) if result else None,
            "items": items,
            "observations": observations,
            "effective_reachability": effective_reachability,
        }


def get_current_state(device_names: list[str] | None = None) -> list[dict]:
    names = device_names or [d["device_name"] for d in list_devices()]
    state = []
    for name in names:
        bundle = get_current_device_bundle(name)
        state.extend(bundle["observations"])
    return state


def get_current_device_status(device_names: list[str] | None = None) -> list[dict]:
    names = device_names or [d["device_name"] for d in list_devices()]
    statuses = []
    for name in names:
        bundle = get_current_device_bundle(name)
        device = bundle["device"]
        result = bundle["result"]
        statuses.append(
            {
                "device_name": name,
                "device_id": device["device_id"] if device else None,
                "site": device["site"] if device else None,
                "reachability": (
                    bundle.get("effective_reachability")
                    or (result["reachability"] if result else "unknown")
                ),
                "overall_status": result["overall_status"] if result else "no_data",
                "last_collected_at": (
                    result["finished_at_utc"] or result["started_at_utc"]
                ) if result else None,
                "run_id": result["run_id"] if result else None,
                "observation_count": len(bundle["observations"]),
                "error": result["error_message"] if result else None,
            }
        )
    return statuses


def get_all_latest_status() -> list[dict]:
    rows = []
    for status in get_current_device_status():
        bundle = get_current_device_bundle(status["device_name"])
        items = bundle["items"]
        if status["reachability"] != "reachable":
            rows.append(
                {
                    "id": None,
                    "device_name": status["device_name"],
                    "command": "device_reachability",
                    "status": "failed" if status["overall_status"] == "failed" else "unknown",
                    "collected_at": status["last_collected_at"],
                    "fail_reason": status["error"],
                    "source": "collector",
                    "capture_type": "reachability",
                    "run_id": status["run_id"],
                    "result_id": None,
                }
            )
        if items:
            for item in items:
                rows.append(
                    {
                        "id": item["item_id"],
                        "device_name": status["device_name"],
                        "command": item.get("command_or_tool") or item.get("capture_type"),
                        "status": "success" if item.get("collection_status") == "success" else "failed",
                        "collected_at": item.get("finished_at_utc") or item.get("started_at_utc"),
                        "fail_reason": item.get("error_message"),
                        "source": item.get("source"),
                        "capture_type": item.get("capture_type"),
                        "run_id": status["run_id"],
                        "result_id": item.get("result_id"),
                    }
                )
        elif status["reachability"] == "reachable":
            rows.append(
                {
                    "id": None,
                    "device_name": status["device_name"],
                    "command": "device_reachability",
                    "status": "failed" if status["overall_status"] == "failed" else "unknown",
                    "collected_at": status["last_collected_at"],
                    "fail_reason": status["error"],
                    "source": "collector",
                    "capture_type": "reachability",
                    "run_id": status["run_id"],
                    "result_id": None,
                }
            )
    return rows


def get_latest_record(device_name: str, command: str = None):
    with get_conn() as conn:
        if command:
            row = conn.execute(
                """SELECT * FROM collection_records
                   WHERE device_name=? AND command=?
                   ORDER BY julianday(collected_at) DESC LIMIT 1""",
                (device_name, command),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM collection_records
                   WHERE device_name=?
                   ORDER BY julianday(collected_at) DESC LIMIT 1""",
                (device_name,),
            ).fetchone()
    return dict(row) if row else None


def get_device_collection_history(device_name: str, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM collection_records
               WHERE device_name=? ORDER BY julianday(collected_at) DESC LIMIT ?""",
            (device_name, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _resolve_device_conn(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM devices WHERE device_name=?", (name,)).fetchone()
    if row:
        return row
    return conn.execute(
        """SELECT d.* FROM devices d
           JOIN device_aliases a ON a.device_id=d.device_id
           WHERE a.alias=? LIMIT 1""",
        (name,),
    ).fetchone()


def _node_conn(
    conn: sqlite3.Connection,
    snapshot_id: str,
    node_key: str,
    label: str,
    device_id: str | None,
) -> str:
    row = conn.execute(
        "SELECT node_id FROM topology_nodes WHERE snapshot_id=? AND node_key=?",
        (snapshot_id, node_key),
    ).fetchone()
    if row:
        return row[0]
    node_id = _new_id("node")
    conn.execute(
        """INSERT INTO topology_nodes
           (node_id, snapshot_id, device_id, node_key, label)
           VALUES (?, ?, ?, ?, ?)""",
        (node_id, snapshot_id, device_id, node_key, label),
    )
    return node_id


def _build_topology_conn(conn: sqlite3.Connection, run_id: str) -> str | None:
    rows = conn.execute(
        """SELECT so.*, d.device_name AS local_device
           FROM state_observations so
           JOIN collection_items ci ON ci.item_id=so.item_id
           JOIN device_capture_results dcr ON dcr.result_id=ci.result_id
           JOIN devices d ON d.device_id=so.device_id
           WHERE dcr.run_id=? AND so.state_type='neighbor'
           ORDER BY so.observed_at_utc""",
        (run_id,),
    ).fetchall()
    if not rows:
        return None
    snapshot_id = _new_id("topo")
    conn.execute(
        """INSERT INTO topology_snapshots
           (snapshot_id, run_id, source, created_at_utc, confidence)
           VALUES (?, ?, 'cdp', ?, 'medium')""",
        (snapshot_id, run_id, now_iso()),
    )
    edge_keys = set()
    for row in rows:
        payload = _json_value(row["payload_json"]) or {}
        remote_name = str(payload.get("remote_device") or "").strip()
        local_if = str(payload.get("local_interface") or "").strip()
        remote_if = str(payload.get("remote_interface") or "").strip()
        if not remote_name:
            continue
        local_device = conn.execute(
            "SELECT * FROM devices WHERE device_name=?", (row["local_device"],)
        ).fetchone()
        if not local_device:
            continue
        remote_device = _resolve_device_conn(conn, remote_name)
        local_node = _node_conn(
            conn, snapshot_id, local_device["device_name"],
            local_device["device_name"], local_device["device_id"],
        )
        if remote_device:
            remote_node = _node_conn(
                conn, snapshot_id, remote_device["device_name"],
                remote_device["device_name"], remote_device["device_id"],
            )
        else:
            remote_node = _node_conn(
                conn, snapshot_id, f"unmanaged:{remote_name}",
                remote_name, None,
            )
        a_if = _ensure_interface_conn(conn, local_device["device_id"], local_if)
        b_if = (
            _ensure_interface_conn(conn, remote_device["device_id"], remote_if)
            if remote_device and remote_if
            else None
        )
        key = tuple(sorted([local_node, remote_node])) + (a_if, b_if)
        if key in edge_keys or local_node == remote_node:
            continue
        edge_keys.add(key)
        conn.execute(
            """INSERT OR IGNORE INTO topology_edges
               (edge_id, snapshot_id, a_node_id, b_node_id, a_interface_id,
                b_interface_id, link_type, status, source_observation_id, confidence)
               VALUES (?, ?, ?, ?, ?, ?, 'cdp', 'up', ?, 'medium')""",
            (
                _new_id("edge"),
                snapshot_id,
                local_node,
                remote_node,
                a_if,
                b_if,
                row["observation_id"],
            ),
        )
    return snapshot_id


def build_topology_snapshot(run_id: str) -> str | None:
    with get_conn() as conn:
        return _build_topology_conn(conn, run_id)


def get_topology_snapshot(run_id: str | None = None) -> dict | None:
    with get_conn() as conn:
        if run_id:
            row = conn.execute(
                "SELECT * FROM topology_snapshots WHERE run_id=? ORDER BY created_at_utc DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM topology_snapshots ORDER BY created_at_utc DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        nodes = conn.execute(
            "SELECT * FROM topology_nodes WHERE snapshot_id=? ORDER BY node_key",
            (row["snapshot_id"],),
        ).fetchall()
        edges = conn.execute(
            "SELECT * FROM topology_edges WHERE snapshot_id=? ORDER BY edge_id",
            (row["snapshot_id"],),
        ).fetchall()
    return {
        "snapshot": dict(row),
        "nodes": [dict(node) for node in nodes],
        "edges": [dict(edge) for edge in edges],
    }


def _migrate_legacy_devices(conn: sqlite3.Connection) -> None:
    for table in _legacy_tables(conn, "devices_legacy"):
        columns = _columns(conn, table)
        if "device_name" not in columns:
            continue
        for row in conn.execute(f'SELECT * FROM "{table}"').fetchall():
            item = dict(row)
            if "device_id" in columns and item.get("device_id"):
                continue
            item.setdefault("device_key", item.get("device_name"))
            raw_json = item.get("raw_json")
            if raw_json:
                try:
                    item.update(json.loads(raw_json))
                except (TypeError, ValueError):
                    pass
            item["device_name"] = row["device_name"]
            _insert_device_conn(conn, item)


def _migrate_legacy_collection(conn: sqlite3.Connection) -> None:
    legacy_tables = _legacy_tables(conn, "collection_records_legacy")
    if not legacy_tables:
        return
    if conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0]:
        return
    records = []
    for table in legacy_tables:
        for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall():
            item = dict(row)
            records.append(
                {
                    "device_name": item.get("device_name"),
                    "status": item.get("status") or "failed",
                    "command": item.get("command"),
                    "raw_output": item.get("raw_output"),
                    "parsed": item.get("parsed_json"),
                    "fail_reason": item.get("fail_reason"),
                    "collected_at": item.get("collected_at"),
                }
            )
    if not records:
        return
    _insert_collection_batch_conn(
        conn,
        records,
        "migration",
        "full",
        "legacy_migration",
        None,
        "batch",
    )


def _seed_command_policies(conn: sqlite3.Connection) -> None:
    from collector.whitelist_commands import READONLY_COMMANDS

    for device_type, commands in READONLY_COMMANDS.items():
        for operation_key, command in commands.items():
            if not command:
                continue
            policy_id = _stable_id("policy", f"{device_type}:{operation_key}:phase1")
            conn.execute(
                """INSERT OR IGNORE INTO command_policies
                   (policy_id, device_type, operation_key, command_template,
                    read_only, timeout_ms, enabled, version)
                   VALUES (?, ?, ?, ?, 1, 15000, 1, 'phase1')""",
                (policy_id, device_type, operation_key, command),
            )
    conn.execute(
        """INSERT OR IGNORE INTO command_policies
           (policy_id, device_type, operation_key, command_template,
            read_only, timeout_ms, enabled, version)
           VALUES (?, 'linux', 'ping_target', 'ping -c 4 -W 2 {target}',
                   1, 15000, 1, 'phase1')""",
        (_stable_id("policy", "linux:ping_target:phase1"),),
    )


def _normalize_existing_timestamps(conn: sqlite3.Connection) -> None:
    """把一期旧数据中的本地时区时间统一转换为 UTC。"""
    table_columns = {
        "sites": ("created_at_utc",),
        "devices": ("created_at_utc", "updated_at_utc"),
        "device_aliases": ("created_at_utc",),
        "device_endpoints": ("last_verified_at_utc",),
        "interfaces": ("first_seen_at_utc", "last_seen_at_utc"),
        "inventory_versions": ("captured_at_utc",),
        "raw_blobs": ("created_at_utc",),
        "collection_runs": (
            "started_at_utc",
            "finished_at_utc",
            "published_at_utc",
            "created_at_utc",
        ),
        "device_capture_results": (
            "started_at_utc",
            "finished_at_utc",
            "published_at_utc",
        ),
        "collection_items": (
            "started_at_utc",
            "finished_at_utc",
            "created_at_utc",
        ),
        "state_observations": ("observed_at_utc", "created_at_utc"),
        "config_snapshots": ("captured_at_utc",),
        "topology_snapshots": ("created_at_utc",),
        "diagnosis_records": ("created_at",),
        "manual_actions": ("created_at",),
        "system_status": ("updated_at",),
    }
    for table, columns in table_columns.items():
        if not _table_exists(conn, table):
            continue
        column_sql = ", ".join(columns)
        rows = conn.execute(
            f'SELECT rowid, {column_sql} FROM "{table}"'
        ).fetchall()
        for row in rows:
            updates = {}
            for column in columns:
                normalized = _to_utc_iso(row[column])
                if normalized != row[column]:
                    updates[column] = normalized
            if not updates:
                continue
            assignments = ", ".join(f"{column}=?" for column in updates)
            conn.execute(
                f'UPDATE "{table}" SET {assignments} WHERE rowid=?',
                (*updates.values(), row["rowid"]),
            )


def _ensure_compatibility_view(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "collection_records"):
        return
    if _view_exists(conn, "collection_records"):
        conn.execute("DROP VIEW collection_records")
    conn.execute(
        """CREATE VIEW collection_records AS
           SELECT ci.item_id AS id,
                  d.device_name AS device_name,
                  COALESCE(ci.finished_at_utc, ci.started_at_utc) AS collected_at,
                  CASE WHEN ci.collection_status='success' THEN 'success' ELSE 'failed' END AS status,
                  ci.error_message AS fail_reason,
                  ci.command_or_tool AS command,
                  rb.content_text AS raw_output,
                  ci.parsed_json AS parsed_json
           FROM collection_items ci
           JOIN devices d ON d.device_id=ci.device_id
           LEFT JOIN raw_blobs rb ON rb.blob_id=ci.raw_blob_id"""
    )


def _prepare_schema(conn: sqlite3.Connection) -> None:
    if _needs_legacy_migration(conn):
        _rename_legacy_tables(conn)
    conn.executescript(BASE_SCHEMA)
    needs_time_normalization = not conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=2"
    ).fetchone()
    _migrate_legacy_devices(conn)
    _migrate_legacy_collection(conn)
    _seed_command_policies(conn)
    if needs_time_normalization:
        _normalize_existing_timestamps(conn)
    _ensure_compatibility_view(conn)
    conn.execute(
        """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
           VALUES (?, 'phase1_asset_collection_state', ?)""",
        (1, now_iso()),
    )
    conn.execute(
        """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
           VALUES (?, 'normalize_utc_timestamps', ?)""",
        (SCHEMA_VERSION, now_iso()),
    )


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _backup_before_migration(conn)
        conn.execute("BEGIN")
        _prepare_schema(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_diagnosis(question, model_id, prompt_template_id, llm_params,
                      retrieved_doc_ids, evidence, answer, use_kb=1, response_time_ms=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO diagnosis_records
               (created_at, question, model_id, prompt_template_id, llm_params_json,
                retrieved_doc_ids_json, evidence_json, answer_json, use_kb, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), question, model_id, prompt_template_id,
             json.dumps(llm_params, ensure_ascii=False),
             json.dumps(retrieved_doc_ids, ensure_ascii=False),
             json.dumps(evidence, ensure_ascii=False),
             json.dumps(answer, ensure_ascii=False),
             use_kb, response_time_ms),
        )
        return cur.lastrowid


def list_diagnoses(limit=50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM diagnosis_records ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_diagnosis(diagnosis_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM diagnosis_records WHERE id=?", (diagnosis_id,)
        ).fetchone()
    return dict(row) if row else None


def insert_manual_action(diagnosis_id, operator, action_desc, backup_ref,
                          recheck_result, status):
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
            rows = conn.execute(
                "SELECT * FROM manual_actions ORDER BY id DESC"
            ).fetchall()
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
