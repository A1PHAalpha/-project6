"""Versioned console API router.

The DTOs in this module intentionally differ from SQLite rows. This lets the
frontend evolve independently while the existing Jinja routes keep using the
legacy API surface.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from collector import db
from collector.run_collect import run_collection
from knowledge_base.build_index import build_index
from webapp import environment_settings
from webapp.report_export import export_diagnosis_report

router = APIRouter(prefix="/api/v1", tags=["console-v1"])


class ApiProblem(Exception):
    def __init__(self, status: int, code: str, message: str, *, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:16]}"


def problem_response(request: Request, exc: ApiProblem) -> JSONResponse:
    body = {
        "code": exc.code,
        "message": exc.message,
        "request_id": _request_id(request),
    }
    if exc.detail is not None:
        body["detail"] = exc.detail
    return JSONResponse(body, status_code=exc.status)


def register_problem_handler(app) -> None:
    app.add_exception_handler(ApiProblem, problem_response)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _duration_seconds(started: str | None, finished: str | None) -> float | None:
    if not started:
        return None
    try:
        start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end = datetime.fromisoformat((finished or _utc_now()).replace("Z", "+00:00"))
        return round(max(0.0, (end - start).total_seconds()), 1)
    except ValueError:
        return None


def _device_fact(observation: dict) -> dict:
    payload = observation.get("payload") or {}
    state_type = observation.get("state_type") or "command_output"
    display_type = (
        state_type
        if state_type
        in ("reachability", "interface", "route", "vlan", "acl", "neighbor", "system")
        else "system"
    )
    value = payload.get("summary")
    if not value:
        for key in ("status", "reachable", "admin_status", "oper_status", "value"):
            if payload.get(key) is not None:
                value = payload[key]
                break
    if value is None:
        value = json.dumps(payload, ensure_ascii=False) if payload else "已观测"
    text = str(value)
    lower = text.lower()
    tone = (
        "danger"
        if (
            observation.get("observation_status") in ("unreachable", "error")
            or lower in ("down", "false", "failed", "unreachable")
        )
        else "warning"
        if lower in ("partial", "warning", "admin_down")
        else "success"
    )
    return {
        "id": observation.get("observation_id") or f"{state_type}:{observation.get('entity_key')}",
        "type": display_type,
        "label": observation.get("entity_key") or state_type,
        "value": text[:300],
        "observedAt": observation.get("observed_at_utc") or _utc_now(),
        "tone": tone,
        "runId": observation.get("run_id"),
        "resultId": observation.get("result_id"),
    }


def _device_with_facts(item: dict) -> dict:
    bundle = db.get_current_device_bundle(item["name"])
    facts = [_device_fact(observation) for observation in bundle.get("observations", [])]
    if not facts:
        facts = [
            {
                "id": f"no-data:{item['id']}",
                "type": "system",
                "label": "采集状态",
                "value": "未采集 · 不推断为故障",
                "observedAt": item.get("collectedAt") or _utc_now(),
                "tone": "neutral",
                "runId": None,
                "resultId": None,
            }
        ]
    raw = db._json_value(bundle.get("device", {}).get("raw_json")) or {}
    interfaces = raw.get("interfaces") or []
    return {
        **item,
        "facts": facts,
        "interfaces": interfaces,
        "runId": (bundle.get("result") or {}).get("run_id"),
        "collectedAt": (
            (bundle.get("result") or {}).get("finished_at_utc")
            or (bundle.get("result") or {}).get("started_at_utc")
        ),
    }


def _run_dto(row: dict) -> dict:
    summary = row.get("summary") or db._json_value(row.get("summary_json")) or {}
    device_count = row.get("device_count")
    if device_count is None:
        device_count = summary.get("device_count", 0)
    scope = row.get("scope") or {}
    scope_names = scope.get("device_names") if isinstance(scope, dict) else None
    return {
        "id": row.get("run_id"),
        "trigger": row.get("trigger_type") or "manual",
        "scope": (
            "全网"
            if row.get("scope_type") == "batch"
            else ", ".join(scope_names)
            if scope_names
            else "指定设备"
        ),
        "status": row.get("status") or "running",
        "deviceSummary": (
            f"{row.get('success_count', 0)} 成功 · "
            f"{row.get('failed_count', 0)} 失败 · "
            f"{row.get('skipped_count', 0)} 跳过"
            if device_count
            else f"{device_count} 台"
        ),
        "sshSummary": (
            f"{summary.get('ssh_commands_success', 0)} 成功 · "
            f"{summary.get('ssh_commands_failed', 0)} 失败"
        ),
        "connectivitySummary": (
            f"{summary.get('connectivity_successes', 0)} 成功 · "
            f"{summary.get('connectivity_failures', 0)} 失败"
        ),
        "startedAt": row.get("started_at_utc"),
        "publishedAt": row.get("published_at_utc"),
        "durationSeconds": _duration_seconds(
            row.get("started_at_utc"),
            row.get("finished_at_utc"),
        ),
        "deviceCount": device_count,
        "successCount": row.get("success_count", 0),
        "failedCount": row.get("failed_count", 0),
        "skippedCount": row.get("skipped_count", 0),
        "summary": summary,
    }


def _knowledge_summary() -> dict:
    catalog = db.knowledge_catalog_summary()
    runtime = db.get_system_status("knowledge_base") or {}
    active = catalog.get("active_index") or {}
    article_count = catalog.get("articles", 0)
    pending = 0 if active.get("status") == "ready" else article_count
    model_name = (
        active.get("model_name")
        or os.getenv("EMBEDDING_MODEL")
        or "BAAI/bge-small-zh-v1.5"
    )
    return {
        "status": runtime.get("status") or active.get("status") or "missing",
        "indexVersionId": active.get("index_version_id"),
        "articleCount": article_count,
        "sourceCount": catalog.get("sources", 0),
        "chunkCount": catalog.get("chunks", 0),
        "pendingCount": pending,
        "modelName": model_name,
        "modelDimensions": active.get("dimension"),
        "checkedAt": runtime.get("checked_at") or _utc_now(),
        "indexes": catalog.get("indexes", []),
    }


def _topology_dto() -> dict:
    devices = db.list_active_devices()
    status_rows = db.get_current_device_status([device["device_name"] for device in devices])
    statuses = {row["device_name"]: row for row in status_rows}
    snapshots = db.get_topology_snapshot()
    snapshot = snapshots.get("snapshot") if snapshots else None
    sites = []
    for device in devices:
        if device.get("site") not in sites:
            sites.append(device.get("site") or "未分类")
    nodes = []
    for index, device in enumerate(devices):
        status = statuses.get(device["device_name"], {})
        site_index = sites.index(device.get("site") or "未分类")
        same_site = [
            candidate
            for candidate in devices
            if (candidate.get("site") or "未分类") == (device.get("site") or "未分类")
        ]
        local_index = same_site.index(device)
        reachability = status.get("reachability", "unknown")
        summary = (
            "未采集"
            if reachability == "unknown"
            else "可达"
            if reachability == "reachable"
            else "不可达"
        )
        nodes.append(
            {
                "id": device["device_id"],
                "deviceName": device["device_name"],
                "role": device.get("role") or device.get("device_type") or "网络设备",
                "site": device.get("site") or "未分类",
                "managementIp": device.get("mgmt_ip") or "未配置",
                "reachability": reachability,
                "summary": summary,
                "x": 100 + site_index * 315,
                "y": 88 + local_index * 145,
            }
        )
    node_by_snapshot = {
        item.get("node_id"): item for item in (snapshots or {}).get("nodes", [])
    }
    edges = []
    for edge in (snapshots or {}).get("edges", []):
        source = node_by_snapshot.get(edge.get("a_node_id"), {}).get("device_id")
        target = node_by_snapshot.get(edge.get("b_node_id"), {}).get("device_id")
        if not source or not target:
            continue
        edges.append(
            {
                "id": edge.get("edge_id"),
                "source": source,
                "target": target,
                "label": edge.get("link_type") or "链路",
                "status": (
                    "up"
                    if edge.get("status") == "up"
                    else "down"
                    if edge.get("status") == "down"
                    else "unknown"
                ),
            }
        )
    return {
        "runId": snapshot.get("run_id") if snapshot else None,
        "publishedAt": snapshot.get("created_at_utc") if snapshot else None,
        "nodes": nodes,
        "edges": edges,
    }


def _sessions_for_console(limit: int = 8) -> list[dict]:
    sessions = db.list_agent_sessions(limit=limit)
    result = []
    for session in sessions:
        detail = _session_detail_dto(session["session_id"])
        if detail:
            result.append(detail)
    return result


def _suggestion_dto(answer: dict, evidence: list[dict]) -> dict | None:
    raw = answer.get("fix_suggestions") or []
    actions: list[str] = []
    commands: list[str] = []
    target_device: str | None = None
    for item in raw:
        if isinstance(item, str):
            actions.append(item)
            continue
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        command = str(item.get("command") or "").strip()
        if action:
            actions.append(action)
        if command:
            commands.append(command)
        if not target_device and item.get("target_device"):
            target_device = str(item["target_device"])
    check_steps = [
        str(step)
        for step in (answer.get("check_steps") or [])
        if str(step).strip()
    ]
    if not actions and check_steps:
        actions = check_steps
    if not target_device:
        devices = {
            str(item.get("device_name"))
            for item in evidence
            if isinstance(item, dict) and item.get("device_name")
        }
        if len(devices) == 1:
            target_device = next(iter(devices))
    if not actions and not commands:
        return None
    return {
        "title": actions[0] if actions else "按证据执行检查或修复步骤",
        "actions": actions,
        "command": commands,
        "targetDevice": target_device,
        "canExecute": bool(commands and target_device),
        "note": "模型仅提供建议。执行前请确认配置备份，并由人工决定是否下发。",
        "requiresManualConfirmation": True,
    }


def _session_detail_dto(session_id: str) -> dict | None:
    detail = db.get_agent_session(session_id)
    if not detail:
        return None
    session = detail["session"]
    messages = [
        {
            "id": message["message_id"],
            "role": message["role"],
            "createdAt": message["created_at_utc"],
            "content": message.get("content_text") or "",
        }
        for message in detail.get("messages", [])
    ]
    last_user = next(
        (
            message["content"]
            for message in reversed(messages)
            if message["role"] == "user" and message["content"]
        ),
        session.get("title") or "诊断会话",
    )
    conclusion = (detail.get("conclusions") or [{}])[-1]
    with db.get_conn() as conn:
        diagnosis = conn.execute(
            """SELECT * FROM diagnosis_records
               WHERE session_id=? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        hits = conn.execute(
            """SELECT rh.*
               FROM retrieval_hits rh
               JOIN retrieval_queries rq ON rq.query_id=rh.query_id
               WHERE rq.session_id=?
               ORDER BY rh.rank_no
               LIMIT 8""",
            (session_id,),
        ).fetchall()
    evidence = (
        db._json_value(diagnosis["evidence_json"]) or [] if diagnosis else []
    )
    answer = db._json_value(diagnosis["answer_json"]) or {} if diagnosis else {}
    causes = []
    for cause in answer.get("possible_causes") or []:
        if isinstance(cause, dict):
            causes.append(
                {
                    "title": cause.get("cause") or cause.get("title") or "待确认原因",
                    "basis": cause.get("basis") or cause.get("evidence") or "依据当前证据",
                    "tone": "warning" if len(causes) else "danger",
                }
            )
    tool_results = {
        item.get("tool_call_id"): item for item in detail.get("tool_results", [])
    }
    tool_calls = []
    for call in detail.get("tool_calls", []):
        result = tool_results.get(call.get("tool_call_id"), {})
        arguments = db._json_value(call.get("arguments_json")) or {}
        device_name = arguments.get("device_name")
        if not device_name:
            names = arguments.get("device_names") or []
            device_name = names[0] if names else None
        tool_calls.append(
            {
                "id": call.get("tool_call_id"),
                "name": call.get("tool_name") or "tool",
                "deviceName": device_name,
                "target": json.dumps(
                    arguments,
                    ensure_ascii=False,
                )[:120],
                "status": (
                    "success"
                    if call.get("status") == "success"
                    else "failed"
                    if call.get("status") == "failed"
                    else result.get("status") == "success"
                    and "success"
                    or "failed"
                ),
            }
        )
    return {
        "id": session["session_id"],
        "title": session.get("title") or last_user,
        "question": last_user,
        "status": session.get("status") or "active",
        "modelId": session.get("model_id") or "unknown",
        "createdAt": session.get("started_at_utc"),
        "updatedAt": session.get("updated_at_utc"),
        "evidence": [
            {
                "id": str(item.get("id") or f"evidence-{index}"),
                "deviceName": item.get("device_name") or "未知设备",
                "label": item.get("command") or item.get("capture_type") or "设备事实",
                "value": str(
                    item.get("summary")
                    or item.get("output")
                    or item.get("fail_reason")
                    or "已关联"
                )[:220],
                "collectedAt": item.get("collected_at")
                or item.get("observed_at_utc")
                or session.get("started_at_utc"),
                "runId": item.get("run_id") or "未关联批次",
                "tone": (
                    "danger"
                    if item.get("status") in ("failed", "unreachable")
                    else "success"
                ),
            }
            for index, item in enumerate(evidence[:8])
            if isinstance(item, dict)
        ],
        "knowledge": [
            {
                "id": hit["doc_id"],
                "title": hit.get("title") or hit["doc_id"],
                "version": hit.get("version") or "v1",
                "source": hit.get("source") or "内部知识库",
                "distance": hit.get("distance"),
            }
            for hit in (dict(row) for row in hits)
        ],
        "toolCalls": tool_calls,
        "conclusion": (
            conclusion.get("fault_phenomenon")
            or answer.get("fault_phenomenon")
            or "诊断已记录，尚未形成明确结论。"
        ),
        "causes": causes,
        "suggestion": _suggestion_dto(answer, evidence),
        "missingInformation": (
            conclusion.get("missing_information")
            or answer.get("missing_information")
            or "无"
        ),
        "messages": messages,
    }


@router.get("/overview")
def get_overview():
    devices = db.get_current_device_status(
        [device["device_name"] for device in db.list_active_devices()]
    )
    runs = db.list_collection_runs_page(page=1, page_size=5)
    recent_runs = [_run_dto(item) for item in runs["items"]]
    attention = []
    for device in devices:
        if device["reachability"] == "unreachable":
            attention.append(
                {
                    "id": f"unreachable:{device['device_name']}",
                    "deviceName": device["device_name"],
                    "title": f"{device['device_name']} 不可达",
                    "detail": device.get("error") or "最近一次已发布批次不可达",
                    "tone": "danger",
                }
            )
        elif device["reachability"] == "unknown":
            attention.append(
                {
                    "id": f"unknown:{device['device_name']}",
                    "deviceName": device["device_name"],
                    "title": f"{device['device_name']} 未采集",
                    "detail": "缺少管理通道或尚无已发布事实，不推断为故障",
                    "tone": "neutral",
                }
            )
    return {
        "devices": {
            "total": len(devices),
            "reachable": sum(1 for item in devices if item["reachability"] == "reachable"),
            "unreachable": sum(1 for item in devices if item["reachability"] == "unreachable"),
            "unknown": sum(1 for item in devices if item["reachability"] == "unknown"),
        },
        "latestRun": recent_runs[0] if recent_runs else None,
        "knowledge": _knowledge_summary(),
        "attention": attention[:8],
        "recentSessions": _sessions_for_console(),
        "recentRuns": recent_runs,
        "topology": _topology_dto(),
        "generatedAt": _utc_now(),
    }


@router.get("/devices")
def list_devices(
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    site: str | None = None,
    status: str | None = None,
):
    result = db.list_devices_page(
        page=page,
        page_size=page_size,
        search=q,
        site=site,
        status=status,
    )
    result["items"] = [_device_with_facts(item) for item in result["items"]]
    return result


class DeviceCreate(BaseModel):
    name: str
    role: str = ""
    site: str = "未分类"
    vendor: str = ""
    model: str = ""
    deviceType: str = Field(default="unknown", alias="device_type")
    managementIp: str = Field(default="", alias="mgmt_ip")
    sshPort: int = Field(default=22, alias="ssh_port")
    snmpPort: int | None = Field(default=None, alias="snmp_port")
    interfaces: list[str] = Field(default_factory=list)
    notes: str | None = None

    model_config = {"populate_by_name": True}


@router.post("/devices", status_code=201)
def create_device(payload: DeviceCreate, request: Request):
    try:
        return db.create_managed_device(
            payload.model_dump(by_alias=False),
            actor="web",
        )
    except ValueError as exc:
        raise ApiProblem(409, "DEVICE_CONFLICT", str(exc)) from exc


class DevicePatch(BaseModel):
    name: str | None = None
    role: str | None = None
    site: str | None = None
    vendor: str | None = None
    model: str | None = None
    deviceType: str | None = None
    managementIp: str | None = None
    sshPort: int | None = None
    snmpPort: int | None = None
    interfaces: list[str] | None = None
    notes: str | None = None


@router.patch("/devices/{device_id}")
def update_device(device_id: str, payload: DevicePatch):
    try:
        result = db.update_managed_device(
            device_id,
            payload.model_dump(exclude_none=True),
            actor="web",
        )
        return _device_with_facts(result)
    except LookupError as exc:
        raise ApiProblem(404, "DEVICE_NOT_FOUND", str(exc)) from exc
    except ValueError as exc:
        raise ApiProblem(409, "DEVICE_CONFLICT", str(exc)) from exc


class ArchiveRequest(BaseModel):
    device_ids: list[str] = Field(default_factory=list, alias="deviceIds")
    names: list[str] = Field(default_factory=list)
    reason: str | None = None

    model_config = {"populate_by_name": True}


@router.post("/devices/archive")
def archive_devices(payload: ArchiveRequest):
    refs = payload.device_ids or payload.names
    try:
        return db.archive_devices(refs, actor="web", reason=payload.reason)
    except ValueError as exc:
        raise ApiProblem(400, "DEVICE_SELECTION_REQUIRED", str(exc)) from exc


@router.get("/devices/{device_id}/current-state")
def current_device_state(device_id: str):
    device = db.get_device(device_id, include_retired=True)
    if not device:
        raise ApiProblem(404, "DEVICE_NOT_FOUND", "设备不存在")
    item = db.device_api_dto(device)
    return _device_with_facts(item)


@router.get("/inventory/export")
def export_inventory():
    return db.export_inventory()


class InventoryImport(BaseModel):
    devices: list[dict]


@router.post("/inventory/import")
def import_inventory(payload: InventoryImport):
    try:
        return db.import_inventory(payload.model_dump(), actor="web")
    except ValueError as exc:
        raise ApiProblem(409, "INVENTORY_IMPORT_FAILED", str(exc)) from exc


@router.get("/collection-runs")
def list_collection_runs(page: int = 1, page_size: int = 20):
    result = db.list_collection_runs_page(page=page, page_size=page_size)
    result["items"] = [_run_dto(item) for item in result["items"]]
    return result


@router.get("/collection-runs/{run_id}")
def collection_run_detail(run_id: str):
    result = db.get_collection_run(run_id)
    if not result:
        raise ApiProblem(404, "COLLECTION_RUN_NOT_FOUND", "采集批次不存在")
    return result


class CollectionCreate(BaseModel):
    trigger: str = "manual"
    device_names: list[str] | None = None
    device_ids: list[str] | None = None
    requested_by: str = "web"


def _run_collection_job(job_id: str, device_names: list[str] | None, actor: str):
    try:
        db.update_job(job_id, status="running", progress=10, message="正在采集设备")
        result = run_collection(
            ",".join(device_names) if device_names else None,
            trigger_type="manual",
            requested_by=actor,
            source_ref=job_id,
        )
        run = db.get_collection_run(result.get("run_id")) or {}
        run_status = run.get("status", "success")
        job_status = (
            "success"
            if run_status == "success"
            else "partial"
            if run_status == "partial"
            else "failed"
        )
        db.update_job(
            job_id,
            status=job_status,
            progress=100,
            message={
                "success": "采集完成",
                "partial": "采集部分完成",
                "failed": "采集失败",
            }[job_status],
            result=result,
        )
    except Exception as exc:
        db.update_job(
            job_id,
            status="failed",
            progress=100,
            message="采集失败",
            error_code=type(exc).__name__,
            result={"error": f"{type(exc).__name__}: {exc}"},
        )


@router.post("/collection-runs", status_code=202)
def create_collection_run(payload: CollectionCreate):
    names = payload.device_names or []
    if payload.device_ids:
        names.extend(
            device["device_name"]
            for device_id in payload.device_ids
            if (device := db.get_device(device_id, include_retired=False))
        )
    if names:
        for name in names:
            if not db.get_device(name, include_retired=False):
                raise ApiProblem(404, "DEVICE_NOT_FOUND", f"设备不存在或已退役: {name}")
    job = db.create_job(
        "collection",
        request={"devices": names},
        created_by=payload.requested_by,
    )
    thread = threading.Thread(
        target=_run_collection_job,
        args=(job["job_id"], names or None, payload.requested_by),
        name=f"collection-{job['job_id']}",
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"], "status_url": f"/api/v1/jobs/{job['job_id']}"}


DEFAULT_SCHEDULE = {
    "enabled": False,
    "frequency_minutes": 30,
    "active_window": {"start": "00:00", "end": "23:59"},
    "scope": "all",
    "concurrency": 4,
    "timeout_seconds": 15,
    "retries": 1,
    "channels": {"ssh": True, "snmp": True, "connectivity": True},
}

SCHEDULER_AVAILABLE = False


def _schedule_response(value: dict) -> dict:
    return {**value, "scheduler_available": SCHEDULER_AVAILABLE}


@router.get("/collection-schedule")
def get_collection_schedule():
    return _schedule_response(
        db.get_application_setting("collection_schedule", DEFAULT_SCHEDULE)
    )


class ScheduleUpdate(BaseModel):
    enabled: bool = False
    frequency_minutes: int = Field(default=30, ge=1, le=1440)
    active_window: dict = Field(default_factory=lambda: {"start": "00:00", "end": "23:59"})
    scope: str = "all"
    concurrency: int = Field(default=4, ge=1, le=32)
    timeout_seconds: int = Field(default=15, ge=1, le=300)
    retries: int = Field(default=1, ge=0, le=5)
    channels: dict = Field(default_factory=dict)


@router.put("/collection-schedule")
def update_collection_schedule(payload: ScheduleUpdate):
    value = payload.model_dump()
    db.set_application_setting("collection_schedule", value, actor="web")
    return _schedule_response(value)


@router.get("/topology")
def get_topology():
    return _topology_dto()


@router.get("/knowledge-base/status")
def knowledge_status():
    return _knowledge_summary()


@router.get("/knowledge-base/articles")
def knowledge_articles(
    page: int = 1,
    page_size: int = 10,
    q: str | None = None,
    category: str | None = None,
):
    return db.list_knowledge_articles_page(
        page=page,
        page_size=page_size,
        search=q,
        category=category,
    )


class KnowledgeRebuild(BaseModel):
    mode: str = "incremental"


def _rebuild_knowledge_job(job_id: str, rebuild: bool) -> None:
    try:
        db.update_job(job_id, status="running", progress=8, message="正在读取知识目录")
        count = build_index(rebuild=rebuild)
        db.update_job(
            job_id,
            status="success",
            progress=100,
            message="知识索引已发布",
            result={"article_count": count},
        )
    except Exception as exc:
        db.update_job(
            job_id,
            status="failed",
            progress=100,
            message="知识索引构建失败，旧索引保持不变",
            error_code=type(exc).__name__,
            result={"error": f"{type(exc).__name__}: {exc}"},
        )


@router.post("/knowledge-base/rebuild", status_code=202)
def rebuild_knowledge(payload: KnowledgeRebuild):
    if payload.mode not in ("incremental", "full"):
        raise ApiProblem(400, "INVALID_REBUILD_MODE", "mode 必须是 incremental 或 full")
    job = db.create_job(
        "knowledge_rebuild",
        request={"mode": payload.mode},
        created_by="web",
    )
    thread = threading.Thread(
        target=_rebuild_knowledge_job,
        args=(job["job_id"], payload.mode == "full"),
        name=f"knowledge-{job['job_id']}",
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"], "progress": 0}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise ApiProblem(404, "JOB_NOT_FOUND", "任务不存在")
    return job


@router.get("/settings/environment")
def get_environment_settings():
    return environment_settings.read_settings()


class EnvironmentUpdate(BaseModel):
    values: dict[str, Any]


@router.put("/settings/environment")
def update_environment_settings(payload: EnvironmentUpdate):
    try:
        return environment_settings.save_settings(payload.values)
    except ValueError as exc:
        raise ApiProblem(400, "ENVIRONMENT_KEY_NOT_ALLOWED", str(exc)) from exc


@router.post("/settings/llm/test")
def test_llm_settings():
    return environment_settings.test_llm_connection()


@router.post("/settings/embedding/test")
def test_embedding_settings():
    return environment_settings.test_embedding()


@router.get("/diagnoses")
def list_diagnoses(limit: int = 50):
    return db.list_diagnoses(limit=limit)


@router.get("/sessions")
def list_sessions(limit: int = 50):
    return _sessions_for_console(limit)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = _session_detail_dto(session_id)
    if not session:
        raise ApiProblem(404, "SESSION_NOT_FOUND", "诊断会话不存在")
    return session


def _latest_diagnosis_id_for_session(session_id: str) -> int | None:
    db.init_db()
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM diagnosis_records
               WHERE session_id=? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
    return int(row["id"]) if row else None


@router.get("/sessions/{session_id}/report")
def export_session_report(session_id: str):
    diagnosis_id = _latest_diagnosis_id_for_session(session_id)
    if diagnosis_id is None:
        raise ApiProblem(404, "SESSION_REPORT_NOT_FOUND", "该会话还没有可导出的诊断记录")
    path = export_diagnosis_report(diagnosis_id)
    if not path:
        raise ApiProblem(404, "DIAGNOSIS_NOT_FOUND", "诊断记录不存在")
    db.record_audit_event(
        actor="web",
        action="report.export",
        target_type="session",
        target_id=session_id,
        details={"diagnosis_id": diagnosis_id, "path": path},
    )
    return FileResponse(
        path,
        filename=os.path.basename(path),
        media_type="text/markdown",
    )


class DiagnoseConsoleRequest(BaseModel):
    question: str
    use_kb: bool = True
    session_id: str | None = None
    requested_by: str = "web"


@router.post("/diagnose")
def diagnose_console(payload: DiagnoseConsoleRequest):
    from rag_engine.diagnosis_engine import diagnose

    try:
        return diagnose(
            payload.question,
            use_kb=payload.use_kb,
            session_id=payload.session_id,
            requested_by=payload.requested_by,
        )
    except Exception as exc:
        raise ApiProblem(
            502,
            "DIAGNOSIS_FAILED",
            "诊断服务执行失败",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
