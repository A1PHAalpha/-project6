"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from ._core import (
    _is_after,
    _json_text,
    _json_value,
    _new_id,
    _sha256,
    _stable_id,
    _to_utc_iso,
    get_conn,
    init_db,
    now_iso,
)
from .assets import (
    _ensure_device_conn,
    _ensure_interface_conn,
    _store_blob_conn,
    device_api_dto,
    list_active_devices,
)


def list_collection_runs_page(*, page: int = 1, page_size: int = 20) -> dict:
    init_db()
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    init_db()
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]
        rows = conn.execute(
            """SELECT r.*,
                      (SELECT COUNT(*) FROM device_capture_results d
                       WHERE d.run_id=r.run_id) AS device_count,
                      (SELECT COUNT(*) FROM device_capture_results d
                       WHERE d.run_id=r.run_id AND d.overall_status='success') AS success_count,
                      (SELECT COUNT(*) FROM device_capture_results d
                       WHERE d.run_id=r.run_id AND d.overall_status='failed') AS failed_count,
                      (SELECT COUNT(*) FROM device_capture_results d
                       WHERE d.run_id=r.run_id AND d.overall_status='skipped') AS skipped_count
               FROM collection_runs r
               ORDER BY r.started_at_utc DESC, r.rowid DESC
               LIMIT ? OFFSET ?""",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["summary"] = _json_value(item.pop("summary_json")) or {}
        item["scope"] = _json_value(item.pop("scope_json")) or {}
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_collection_run(run_id: str) -> dict | None:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM collection_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        results = conn.execute(
            """SELECT dcr.*, d.device_name
               FROM device_capture_results dcr
               JOIN devices d ON d.device_id=dcr.device_id
               WHERE dcr.run_id=?
               ORDER BY d.device_name""",
            (run_id,),
        ).fetchall()
    detail = dict(row)
    detail["summary"] = _json_value(detail.pop("summary_json")) or {}
    detail["scope"] = _json_value(detail.pop("scope_json")) or {}
    detail["results"] = [dict(item) for item in results]
    return detail


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
        content_source
        if isinstance(content_source, str)
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
    if collection_status == "success" or (collection_status == "failed" and capture_type == "ping"):
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
    timestamps = [_to_utc_iso(r.get("collected_at")) for r in records if r.get("collected_at")]
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
        started = min((_to_utc_iso(r.get("collected_at")) or timestamp) for r in device_records)
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
            if capture == "ping" and status == "failed" and record.get("raw_output"):
                status = "success"
                observation_status = "observed"
            if observation_status is None:
                observation_status = (
                    "observed" if status == "success" else "unreachable" if capture == "ping" else "error"
                )
            source = record.get("source") or (
                "snmp"
                if (command or "").upper().startswith("SNMP:")
                else "ping"
                if (command or "").lower().startswith("ping")
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
        reachable = "reachable" if device_success else "unreachable" if statuses else "unknown"
        overall = (
            "success"
            if all(s == "success" for s in statuses)
            else ("partial" if any(s == "success" for s in statuses) else "failed")
        )
        finish = max((_to_utc_iso(r.get("collected_at")) or started) for r in device_records)
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
    run_status = "success" if failure_count == 0 else "partial" if success_count else "failed"
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
    scope_type: str | None = None,
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
            scope_type,
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
            result = _latest_result_conn(conn, device["device_id"], ("full", "single", "recheck"))
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
            recheck and (result is None or _is_after(recheck["published_at_utc"], result["published_at_utc"]))
        )
        if recheck and (result is None or recheck["result_id"] != result["result_id"]):
            recheck_obs = _result_observations_conn(conn, recheck["result_id"])
            observations = recheck_obs + observations
        effective_reachability = result["reachability"] if result else "unknown"
        if recheck_newer and recheck:
            effective_reachability = recheck["reachability"]

        if (
            result
            and (
                result["reachability"] == "unreachable"
                or (result["reachability"] == "unknown" and result["overall_status"] == "failed")
            )
            and result["result_type"] != "recheck"
            and not (recheck_newer and recheck["reachability"] == "reachable")
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
    names = device_names or [d["device_name"] for d in list_active_devices()]
    state = []
    for name in names:
        bundle = get_current_device_bundle(name)
        state.extend(bundle["observations"])
    return state


def get_current_device_status(device_names: list[str] | None = None) -> list[dict]:
    names = device_names or [d["device_name"] for d in list_active_devices()]
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
                    bundle.get("effective_reachability") or (result["reachability"] if result else "unknown")
                ),
                "overall_status": result["overall_status"] if result else "no_data",
                "last_collected_at": (result["finished_at_utc"] or result["started_at_utc"]) if result else None,
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
        local_device = conn.execute("SELECT * FROM devices WHERE device_name=?", (row["local_device"],)).fetchone()
        if not local_device:
            continue
        remote_device = _resolve_device_conn(conn, remote_name)
        local_node = _node_conn(
            conn,
            snapshot_id,
            local_device["device_name"],
            local_device["device_name"],
            local_device["device_id"],
        )
        if remote_device:
            remote_node = _node_conn(
                conn,
                snapshot_id,
                remote_device["device_name"],
                remote_device["device_name"],
                remote_device["device_id"],
            )
        else:
            remote_node = _node_conn(
                conn,
                snapshot_id,
                f"unmanaged:{remote_name}",
                remote_name,
                None,
            )
        a_if = _ensure_interface_conn(conn, local_device["device_id"], local_if)
        b_if = (
            _ensure_interface_conn(conn, remote_device["device_id"], remote_if) if remote_device and remote_if else None
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
            row = conn.execute("SELECT * FROM topology_snapshots ORDER BY created_at_utc DESC LIMIT 1").fetchone()
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


def _device_status_map() -> dict[str, dict]:
    return {
        item["device_name"]: item
        for item in get_current_device_status([device["device_name"] for device in list_active_devices()])
    }


def list_devices_page(
    *,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    site: str | None = None,
    status: str | None = None,
) -> dict:
    devices = list_active_devices()
    statuses = _device_status_map()
    rows = [device_api_dto(device, statuses.get(device["device_name"])) for device in devices]
    if search:
        needle = search.strip().lower()
        rows = [
            row
            for row in rows
            if needle
            in " ".join(
                [
                    row["name"],
                    row["role"],
                    row["site"],
                    row["managementIp"],
                    row["model"],
                ]
            ).lower()
        ]
    if site:
        rows = [row for row in rows if row["site"] == site]
    if status:
        rows = [row for row in rows if row["reachability"] == status]
    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


__all__ = (
    "_build_topology_conn",
    "_dedupe_observations",
    "_device_status_map",
    "_insert_collection_batch_conn",
    "_insert_item_conn",
    "_insert_observation_conn",
    "_insert_run_conn",
    "_latest_result_conn",
    "_node_conn",
    "_resolve_device_conn",
    "_result_observations_conn",
    "build_topology_snapshot",
    "create_device_capture_result",
    "finalize_device_capture_result",
    "finish_collection_run",
    "get_all_latest_status",
    "get_collection_run",
    "get_current_device_bundle",
    "get_current_device_status",
    "get_current_state",
    "get_device_collection_history",
    "get_latest_record",
    "get_topology_snapshot",
    "insert_collection_batch",
    "insert_collection_item",
    "insert_collection_record",
    "list_collection_runs_page",
    "list_devices_page",
    "reset_collection_data",
    "start_collection_run",
)
