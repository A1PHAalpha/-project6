"""可审计的只读运维工具。

工具调用本身不执行配置变更。会访问设备的工具只调用白名单采集流程，并把
``tool_call_id`` 写入采集批次的 ``source_ref``，从而把模型请求、工具结果和
设备证据串成同一条审计链。
"""
from __future__ import annotations

from collector import db
from rag_engine.sanitize import sanitize_text


def list_tool_definitions() -> list[dict]:
    return db.list_tools(enabled_only=True)


def _require_string_list(value, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} 必须是字符串数组")
    return [item.strip() for item in value if item.strip()]


def _query_device_state(arguments: dict, _context: dict) -> dict:
    device_names = _require_string_list(
        arguments.get("device_names") or [], "device_names"
    )
    if not device_names:
        device_names = [device["device_name"] for device in db.list_devices()]
    state_types = set(arguments.get("state_types") or [])

    devices = []
    for device_name in device_names:
        bundle = db.get_current_device_bundle(device_name)
        if not bundle.get("device"):
            devices.append(
                {
                    "device_name": device_name,
                    "status": "not_found",
                    "message": "设备不在台账中",
                }
            )
            continue
        observations = []
        for observation in bundle.get("observations") or []:
            if state_types and observation.get("state_type") not in state_types:
                continue
            observations.append(
                {
                    "observation_id": observation.get("observation_id"),
                    "item_id": observation.get("item_id"),
                    "state_type": observation.get("state_type"),
                    "entity_type": observation.get("entity_type"),
                    "entity_key": observation.get("entity_key"),
                    "observed_at_utc": observation.get("observed_at_utc"),
                    "payload": observation.get("payload"),
                    "confidence": observation.get("confidence"),
                    "source": observation.get("source"),
                }
            )
        result = bundle.get("result") or {}
        devices.append(
            {
                "device_name": device_name,
                "reachability": bundle.get("effective_reachability"),
                "overall_status": result.get("overall_status"),
                "run_id": result.get("run_id"),
                "result_id": result.get("result_id"),
                "collected_at": result.get("finished_at_utc")
                or result.get("started_at_utc"),
                "observations": observations,
            }
        )
    return {"device_count": len(devices), "devices": devices}


def _query_collection_history(arguments: dict, _context: dict) -> dict:
    device_name = str(arguments.get("device_name") or "").strip()
    if not device_name:
        raise ValueError("device_name 不能为空")
    limit = max(1, min(int(arguments.get("limit") or 20), 200))
    records = db.get_device_collection_history(device_name, limit=limit)
    return {
        "device_name": device_name,
        "limit": limit,
        "records": [
            {
                "item_id": row.get("id"),
                "collected_at": row.get("collected_at"),
                "status": row.get("status"),
                "command": row.get("command"),
                "fail_reason": row.get("fail_reason"),
                "raw_output": sanitize_text(row.get("raw_output") or ""),
                "parsed": row.get("parsed_json"),
            }
            for row in records
        ],
    }


def _run_readonly_collection(arguments: dict, context: dict) -> dict:
    from collector.run_collect import run_collection

    device_names = _require_string_list(
        arguments.get("device_names") or [], "device_names"
    )
    if not device_names:
        raise ValueError("device_names 不能为空")
    trigger_type = "mcp" if context.get("transport") == "mcp" else "llm_tool"
    summary = run_collection(
        ",".join(device_names),
        trigger_type=trigger_type,
        requested_by=context.get("requested_by") or "tool_call",
        source_ref=context.get("tool_call_id"),
    )
    summary["_collection_run_id"] = summary.get("run_id")
    return summary


_HANDLERS = {
    "query_device_state": _query_device_state,
    "query_collection_history": _query_collection_history,
    "run_readonly_collection": _run_readonly_collection,
}


def invoke_tool(
    tool_name: str,
    arguments: dict | None = None,
    *,
    session_id: str | None = None,
    message_id: str | None = None,
    requested_by: str = "api",
    transport: str = "local",
) -> dict:
    """调用一个已注册的只读工具并持久化请求和结果。"""
    tool = db.get_tool(tool_name)
    if not tool:
        raise ValueError(f"工具未注册: {tool_name}")
    if tool["transport"] not in ("local", "mcp"):
        raise ValueError(f"工具传输方式尚未接通: {tool['transport']}")

    started = db.start_tool_call(
        tool_name,
        arguments or {},
        session_id=session_id,
        message_id=message_id,
    )
    context = {
        "tool_call_id": started["tool_call_id"],
        "session_id": session_id,
        "requested_by": requested_by,
        "transport": transport,
    }
    handler = _HANDLERS.get(tool_name)
    try:
        if handler is None:
            raise ValueError(f"工具没有本地处理器: {tool_name}")
        result = handler(arguments or {}, context)
        collection_run_id = result.pop("_collection_run_id", None)
        return db.finish_tool_call(
            started["tool_call_id"],
            status="success",
            result=result,
            result_kind="collection" if collection_run_id else "json",
            collection_run_id=collection_run_id,
        )
    except Exception as exc:
        db.finish_tool_call(
            started["tool_call_id"],
            status="failed",
            result={"error": f"{type(exc).__name__}: {exc}"},
            result_kind="error",
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
        raise
