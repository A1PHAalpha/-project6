"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ._core import SCHEMA_VERSION, _json_value, _new_id, _sha256, _stable_id, get_conn, init_db, now_iso
from .operations import record_audit_event, set_application_setting


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
    row = conn.execute("SELECT * FROM devices WHERE device_name=?", (device_name,)).fetchone()
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
    existing = conn.execute("SELECT device_id FROM devices WHERE device_key=?", (device_key,)).fetchone()
    device_id = device.get("device_id") or (existing[0] if existing else _stable_id("dev", device_key))
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
    result["site"] = conn.execute("SELECT name FROM sites WHERE site_id=?", (site_id,)).fetchone()[0]
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
        version_id = _upsert_inventory_version_conn(conn, str(json_path), source_hash, imported_by="inventory_loader")
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


def get_device(device_ref: str, *, include_retired: bool = True) -> dict | None:
    """Resolve a stable device id or display name."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT d.*, s.name AS site
               FROM devices d
               LEFT JOIN sites s ON s.site_id=d.site_id
               WHERE d.device_id=? OR d.device_name=?
               ORDER BY CASE WHEN d.device_id=? THEN 0 ELSE 1 END
               LIMIT 1""",
            (device_ref, device_ref, device_ref),
        ).fetchone()
    if not row:
        return None
    device = dict(row)
    if not include_retired and device.get("lifecycle_status") == "retired":
        return None
    return device


def list_active_devices() -> list[dict]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT d.*, s.name AS site
               FROM devices d
               LEFT JOIN sites s ON s.site_id=d.site_id
               WHERE d.lifecycle_status='active'
               ORDER BY s.name, d.device_name"""
        ).fetchall()
    return [dict(row) for row in rows]


def _record_inventory_snapshot_conn(
    conn: sqlite3.Connection,
    *,
    source: str,
    actor: str,
    notes: str | None = None,
) -> str:
    """Persist the current active inventory as an immutable version snapshot."""
    rows = conn.execute(
        """SELECT d.*, s.name AS site
           FROM devices d
           LEFT JOIN sites s ON s.site_id=d.site_id
           WHERE d.lifecycle_status='active'
           ORDER BY d.device_name"""
    ).fetchall()
    items = []
    for row in rows:
        raw = _json_value(row["raw_json"]) or {}
        item = {
            "device_name": row["device_name"],
            "device_key": row["device_key"],
            "device_id": row["device_id"],
            "role": row["role"],
            "site": row["site"],
            "vendor": row["vendor"],
            "model": row["model"],
            "mgmt_ip": row["mgmt_ip"],
            "device_type": row["device_type"],
            "ssh_port": raw.get("ssh_port", 22),
            "snmp_port": raw.get("snmp_port"),
            "interfaces": raw.get("interfaces", []),
        }
        if raw.get("notes"):
            item["notes"] = raw["notes"]
        items.append(item)
    canonical = json.dumps(
        {"devices": items},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    version_id = _upsert_inventory_version_conn(
        conn,
        source,
        source_hash,
        imported_by=actor,
        notes=notes,
    )
    for item in items:
        conn.execute(
            """INSERT INTO inventory_items
               (item_id, version_id, device_key, device_id, attributes_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(version_id, device_key) DO UPDATE SET
                 device_id=excluded.device_id,
                 attributes_json=excluded.attributes_json""",
            (
                _stable_id("invitem", f"{version_id}:{item['device_key']}"),
                version_id,
                item["device_key"],
                item["device_id"],
                json.dumps(item, ensure_ascii=False),
            ),
        )
    conn.execute(
        """INSERT INTO application_settings
           (setting_key, setting_json, updated_by, updated_at_utc)
           VALUES ('active_inventory_version_id', ?, ?, ?)
           ON CONFLICT(setting_key) DO UPDATE SET
             setting_json=excluded.setting_json,
             updated_by=excluded.updated_by,
             updated_at_utc=excluded.updated_at_utc""",
        (json.dumps(version_id), actor, now_iso()),
    )
    return version_id


def device_api_dto(device: dict, status: dict | None = None) -> dict:
    status = status or {}
    device_type = device.get("device_type") or ""
    return {
        "id": device["device_id"],
        "name": device["device_name"],
        "role": device.get("role") or "",
        "site": device.get("site") or "未分类",
        "vendor": device.get("vendor") or "",
        "model": device.get("model") or "",
        "platform": device.get("os_version") or device_type or "unknown",
        "deviceType": device_type,
        "managementIp": device.get("mgmt_ip") or "",
        "reachability": status.get("reachability", "unknown"),
        "overallStatus": status.get("overall_status", "no_data"),
        "collectedAt": status.get("last_collected_at"),
        "runId": status.get("run_id"),
        "facts": status.get("facts", []),
        "interfaces": status.get("interfaces", []),
        "lifecycleStatus": device.get("lifecycle_status", "unknown"),
        "createdAt": device.get("created_at_utc"),
        "updatedAt": device.get("updated_at_utc"),
    }


def _validate_device_input(
    conn: sqlite3.Connection,
    values: dict,
    *,
    exclude_device_id: str | None = None,
) -> None:
    name = (values.get("device_name") or values.get("name") or "").strip()
    ip = (values.get("mgmt_ip") or values.get("managementIp") or "").strip()
    if not name:
        raise ValueError("设备名称不能为空")
    name_row = conn.execute(
        "SELECT device_id FROM devices WHERE device_name=? AND device_id<>?",
        (name, exclude_device_id or ""),
    ).fetchone()
    if name_row:
        raise ValueError(f"设备名称已存在: {name}")
    if ip:
        ip_row = conn.execute(
            """SELECT device_id FROM devices
               WHERE mgmt_ip=? AND lifecycle_status='active' AND device_id<>?""",
            (ip, exclude_device_id or ""),
        ).fetchone()
        if ip_row:
            raise ValueError(f"管理地址已被其他设备使用: {ip}")


def create_managed_device(device: dict, *, actor: str = "web") -> dict:
    payload = {
        "device_name": (device.get("name") or device.get("device_name") or "").strip(),
        "role": (device.get("role") or "").strip() or None,
        "site": (device.get("site") or "").strip() or "未分类",
        "vendor": (device.get("vendor") or "").strip() or None,
        "model": (device.get("model") or "").strip() or None,
        "mgmt_ip": (device.get("managementIp") or device.get("mgmt_ip") or "").strip() or None,
        "device_type": (device.get("deviceType") or device.get("device_type") or "unknown").strip(),
        "ssh_port": int(device.get("sshPort") or device.get("ssh_port") or 22),
        "snmp_port": (int(device["snmpPort"]) if device.get("snmpPort") not in (None, "") else device.get("snmp_port")),
        "interfaces": list(device.get("interfaces") or []),
        "notes": device.get("notes"),
        "lifecycle_status": "active",
    }
    init_db()
    with get_conn() as conn:
        _validate_device_input(conn, payload)
        created = _insert_device_conn(conn, payload)
        version_id = _record_inventory_snapshot_conn(
            conn,
            source="console",
            actor=actor,
            notes=f"create:{created['device_name']}",
        )
    record_audit_event(
        actor=actor,
        action="device.create",
        target_type="device",
        target_id=created["device_id"],
        details={"device_name": created["device_name"], "site": payload["site"]},
    )
    result = device_api_dto(get_device(created["device_id"]) or created)
    result["inventoryVersionId"] = version_id
    return result


def update_managed_device(device_id: str, changes: dict, *, actor: str = "web") -> dict:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            raise LookupError("设备不存在")
        current = dict(row)
        raw = _json_value(current.get("raw_json")) or {}
        field_map = {
            "name": "device_name",
            "deviceName": "device_name",
            "role": "role",
            "site": "site",
            "vendor": "vendor",
            "model": "model",
            "managementIp": "mgmt_ip",
            "mgmt_ip": "mgmt_ip",
            "deviceType": "device_type",
            "device_type": "device_type",
            "osVersion": "os_version",
            "sshPort": "ssh_port",
            "snmpPort": "snmp_port",
            "interfaces": "interfaces",
            "notes": "notes",
        }
        merged = {
            "device_name": current["device_name"],
            "role": current.get("role"),
            "site": (
                conn.execute("SELECT name FROM sites WHERE site_id=?", (current["site_id"],)).fetchone() or ["未分类"]
            )[0],
            "vendor": current.get("vendor"),
            "model": current.get("model"),
            "mgmt_ip": current.get("mgmt_ip"),
            "device_type": current.get("device_type"),
            "os_version": current.get("os_version"),
            "ssh_port": raw.get("ssh_port", 22),
            "snmp_port": raw.get("snmp_port"),
            "interfaces": raw.get("interfaces", []),
            "notes": raw.get("notes"),
            "lifecycle_status": current["lifecycle_status"],
        }
        for key, value in changes.items():
            target = field_map.get(key)
            if target:
                merged[target] = value
        _validate_device_input(conn, merged, exclude_device_id=device_id)
        site_id = _ensure_site_conn(conn, merged["site"])
        timestamp = now_iso()
        raw_payload = {**raw, **merged}
        conn.execute(
            """UPDATE devices
               SET device_name=?, site_id=?, role=?, vendor=?, model=?,
                   os_family=?, os_version=?, mgmt_ip=?, device_type=?,
                   raw_json=?, updated_at_utc=?
               WHERE device_id=?""",
            (
                merged["device_name"],
                site_id,
                merged["role"],
                merged["vendor"],
                merged["model"],
                _os_family(merged["device_type"]),
                merged.get("os_version"),
                merged["mgmt_ip"],
                merged["device_type"],
                json.dumps(raw_payload, ensure_ascii=False),
                timestamp,
                device_id,
            ),
        )
        conn.execute("DELETE FROM device_endpoints WHERE device_id=?", (device_id,))
        if merged.get("mgmt_ip"):
            ssh_port = int(merged.get("ssh_port") or 22)
            conn.execute(
                """INSERT INTO device_endpoints
                   (endpoint_id, device_id, protocol, host, port, credential_ref,
                    enabled, last_verified_at_utc)
                   VALUES (?, ?, 'ssh', ?, ?, NULL, 1, NULL)""",
                (
                    _stable_id("ep", f"{device_id}:ssh:{merged['mgmt_ip']}:{ssh_port}"),
                    device_id,
                    merged["mgmt_ip"],
                    ssh_port,
                ),
            )
            if merged.get("snmp_port"):
                conn.execute(
                    """INSERT INTO device_endpoints
                       (endpoint_id, device_id, protocol, host, port, credential_ref,
                        enabled, last_verified_at_utc)
                       VALUES (?, ?, 'snmp', ?, ?, NULL, 1, NULL)""",
                    (
                        _stable_id("ep", f"{device_id}:snmp:{merged['mgmt_ip']}:{merged['snmp_port']}"),
                        device_id,
                        merged["mgmt_ip"],
                        int(merged["snmp_port"]),
                    ),
                )
        for interface in merged.get("interfaces") or []:
            _ensure_interface_conn(conn, device_id, interface)
        version_id = _record_inventory_snapshot_conn(
            conn,
            source="console",
            actor=actor,
            notes=f"update:{merged['device_name']}",
        )
    record_audit_event(
        actor=actor,
        action="device.update",
        target_type="device",
        target_id=device_id,
        details={"fields": sorted(changes)},
    )
    result = device_api_dto(get_device(device_id) or {})
    result["inventoryVersionId"] = version_id
    return result


def archive_devices(device_refs: list[str], *, actor: str = "web", reason: str | None = None) -> dict:
    refs = [str(value).strip() for value in device_refs if str(value).strip()]
    if not refs:
        raise ValueError("至少选择一台设备")
    init_db()
    archived = []
    with get_conn() as conn:
        timestamp = now_iso()
        for ref in refs:
            row = conn.execute(
                """SELECT * FROM devices
                   WHERE (device_id=? OR device_name=?)
                     AND lifecycle_status<>'retired'""",
                (ref, ref),
            ).fetchone()
            if not row:
                continue
            conn.execute(
                """UPDATE devices
                   SET lifecycle_status='retired', updated_at_utc=?
                   WHERE device_id=?""",
                (timestamp, row["device_id"]),
            )
            conn.execute(
                "UPDATE device_endpoints SET enabled=0 WHERE device_id=?",
                (row["device_id"],),
            )
            archived.append({"device_id": row["device_id"], "device_name": row["device_name"]})
        version_id = _record_inventory_snapshot_conn(
            conn,
            source="console",
            actor=actor,
            notes=f"archive:{len(archived)} devices",
        )
    for item in archived:
        record_audit_event(
            actor=actor,
            action="device.archive",
            target_type="device",
            target_id=item["device_id"],
            details={"device_name": item["device_name"], "reason": reason, "soft_delete": True},
        )
    return {
        "archived": len(archived),
        "items": archived,
        "inventoryVersionId": version_id,
    }


def export_inventory() -> dict:
    devices = []
    for device in list_active_devices():
        raw = _json_value(device.get("raw_json")) or {}
        item = {
            "device_name": device["device_name"],
            "role": device.get("role"),
            "site": device.get("site"),
            "vendor": device.get("vendor"),
            "model": device.get("model"),
            "mgmt_ip": device.get("mgmt_ip"),
            "device_type": device.get("device_type"),
            "ssh_port": raw.get("ssh_port", 22),
            "snmp_port": raw.get("snmp_port"),
            "interfaces": raw.get("interfaces", []),
        }
        if raw.get("notes"):
            item["notes"] = raw["notes"]
        devices.append(item)
    return {"version": SCHEMA_VERSION, "exported_at_utc": now_iso(), "devices": devices}


def import_inventory(payload: dict, *, actor: str = "web") -> dict:
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, list):
        raise ValueError("导入文件必须包含 devices 数组")
    if not devices:
        raise ValueError("导入文件中的 devices 不能为空")
    normalized = []
    seen_names = set()
    for item in devices:
        if not isinstance(item, dict):
            raise ValueError("devices 中的每一项必须是对象")
        name = str(item.get("device_name") or item.get("name") or "").strip()
        if not name or name in seen_names:
            raise ValueError(f"设备名称缺失或重复: {name or '<empty>'}")
        seen_names.add(name)
        normalized.append(
            {
                "device_name": name,
                "role": item.get("role"),
                "site": item.get("site") or "未分类",
                "vendor": item.get("vendor"),
                "model": item.get("model"),
                "mgmt_ip": item.get("mgmt_ip") or item.get("managementIp"),
                "device_type": item.get("device_type") or item.get("deviceType") or "unknown",
                "ssh_port": item.get("ssh_port") or item.get("sshPort") or 22,
                "snmp_port": item.get("snmp_port") or item.get("snmpPort"),
                "interfaces": list(item.get("interfaces") or []),
                "notes": item.get("notes"),
                "lifecycle_status": "active",
            }
        )
    source_hash = _sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True))
    created = []
    init_db()
    with get_conn() as conn:
        version_id = _upsert_inventory_version_conn(
            conn,
            "console_import",
            source_hash,
            imported_by=actor,
            notes="explicit inventory import",
        )
        for item in normalized:
            existing = conn.execute(
                "SELECT device_id FROM devices WHERE device_name=?",
                (item["device_name"],),
            ).fetchone()
            _validate_device_input(
                conn,
                item,
                exclude_device_id=existing["device_id"] if existing else None,
            )
            created_device = _insert_device_conn(conn, item)
            conn.execute(
                """INSERT INTO inventory_items
                   (item_id, version_id, device_key, device_id, attributes_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(version_id, device_key) DO UPDATE SET
                     device_id=excluded.device_id,
                     attributes_json=excluded.attributes_json""",
                (
                    _stable_id("invitem", f"{version_id}:{created_device['device_key']}"),
                    version_id,
                    created_device["device_key"],
                    created_device["device_id"],
                    json.dumps(item, ensure_ascii=False),
                ),
            )
            created.append(device_api_dto(get_device(created_device["device_id"]) or created_device))
    set_application_setting(
        "active_inventory_version_id",
        version_id,
        actor=actor,
    )
    for item in created:
        record_audit_event(
            actor=actor,
            action="inventory.import",
            target_type="device",
            target_id=item["id"],
            details={"device_name": item["name"], "inventory_version_id": version_id},
        )
    return {
        "imported": len(created),
        "inventory_version_id": version_id,
        "items": created,
    }


__all__ = (
    "_ensure_device_conn",
    "_ensure_interface_conn",
    "_ensure_site_conn",
    "_insert_device_conn",
    "_os_family",
    "_record_inventory_snapshot_conn",
    "_site_code",
    "_store_blob_conn",
    "_upsert_inventory_version_conn",
    "_validate_device_input",
    "archive_devices",
    "create_managed_device",
    "device_api_dto",
    "export_inventory",
    "get_device",
    "import_inventory",
    "list_active_devices",
    "list_devices",
    "load_inventory",
    "load_inventory_with_version",
    "update_managed_device",
    "upsert_device",
)
