"""从一期采集快照构建诊断证据。

只读取已发布的 ``device_capture_results``，并按设备的最新采集结果取
``state_observations``。如果设备最近一次采集不可达，则只给出不可达事实，
不会把历史成功状态继续当作当前状态。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from collector import db

EVIDENCE_STALE_MINUTES = 30
MAX_OBSERVATIONS_PER_DEVICE = 80
MAX_EVIDENCE_ITEMS = 30
MAX_EVIDENCE_PER_DEVICE = 12

_FAULT_STATE_PRIORITY = {
    "route": 2,
    "acl": 3,
    "vlan": 4,
    "neighbor": 5,
    "interface": 6,
    "arp": 7,
    "system": 8,
    "config": 9,
    "command_output": 10,
}

_EVIDENCE_STATE_QUOTA = {
    "reachability": 2,
    "interface": 4,
    "route": 4,
    "vlan": 3,
    "acl": 2,
    "neighbor": 3,
    "arp": 2,
    "system": 1,
    "config": 1,
    "command_output": 1,
}
_DEFAULT_STATE_QUOTA = 2

_STATE_PRIORITY = {
    "reachability": 0,
    "route": 1,
    "acl": 2,
    "vlan": 3,
    "neighbor": 4,
    "interface": 5,
    "system": 6,
    "config": 7,
    "arp": 8,
    "dhcp": 9,
    "log": 10,
    "command_output": 11,
}


def _is_stale(collected_at: str | None) -> bool:
    try:
        ts = datetime.fromisoformat(collected_at)
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        return (now - ts) > timedelta(minutes=EVIDENCE_STALE_MINUTES)
    except Exception:
        return False


def _short(value, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _observation_priority(observation: dict) -> int:
    """Lower is more useful for diagnosis; down/unreachable facts come first."""
    state_type = observation.get("state_type")
    payload = observation.get("payload") or {}
    if state_type == "reachability":
        return 0 if payload.get("reachable") is False else 4
    if state_type == "interface":
        status = str(payload.get("status") or payload.get("oper_status") or "").lower()
        admin = str(payload.get("admin_status") or "").lower()
        if "down" in status and "down" not in admin:
            # Link/protocol down while administratively enabled is the fault signal.
            return 0
        if "down" in admin:
            # An administratively down port is often unused; keep it but do not
            # let it crowd out routes, VLANs, ACLs and actual link failures.
            return 7
        return 6
    return _FAULT_STATE_PRIORITY.get(state_type, 10)


def _diversify_bucket(items: list[dict]) -> list[dict]:
    """Prefer at least a few facts from each state category before filling."""
    ordered = sorted(
        items,
        key=lambda item: (item.get("priority", 99), item.get("collected_at") or ""),
    )
    selected: list[dict] = []
    selected_ids: set[int] = set()
    counts: dict[str, int] = {}
    for item in ordered:
        state_type = item.get("state_type") or "other"
        quota = _EVIDENCE_STATE_QUOTA.get(state_type, _DEFAULT_STATE_QUOTA)
        if counts.get(state_type, 0) >= quota:
            continue
        selected.append(item)
        selected_ids.add(id(item))
        counts[state_type] = counts.get(state_type, 0) + 1
    for item in ordered:
        if id(item) not in selected_ids:
            selected.append(item)
    return selected


def select_compact_evidence(
    evidence: list[dict], limit: int = MAX_EVIDENCE_ITEMS
) -> list[dict]:
    """Keep a bounded, fault-focused evidence set while representing every device."""
    if limit <= 0:
        return []
    if len(evidence) <= limit:
        return list(evidence)

    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in evidence:
        device_name = item.get("device_name") or "unknown"
        if device_name not in buckets:
            buckets[device_name] = []
            order.append(device_name)
        buckets[device_name].append(item)

    per_device_limit = MAX_EVIDENCE_PER_DEVICE if len(order) > 1 else limit
    for device_name in order:
        buckets[device_name] = _diversify_bucket(buckets[device_name])

    selected: list[dict] = []
    # Ensure every device with evidence is represented.
    for device_name in order:
        if buckets[device_name] and len(selected) < limit:
            selected.append(buckets[device_name].pop(0))

    # Fill per-device buckets without letting one device crowd out the rest.
    for device_name in order:
        while buckets[device_name] and len(selected) < limit:
            device_count = sum(
                1 for item in selected if item.get("device_name") == device_name
            )
            if device_count >= per_device_limit:
                break
            selected.append(buckets[device_name].pop(0))

    remaining = [item for device_name in order for item in buckets[device_name]]
    remaining.sort(
        key=lambda item: (item.get("priority", 99), item.get("collected_at") or "")
    )
    selected.extend(remaining[: max(0, limit - len(selected))])

    selected.sort(
        key=lambda item: (
            order.index(item.get("device_name") or "unknown"),
            item.get("priority", 99),
        )
    )
    return selected


def _summarize_observation(observation: dict) -> str:
    state_type = observation.get("state_type")
    payload = observation.get("payload") or {}
    entity_key = observation.get("entity_key") or "unknown"

    if state_type == "reachability":
        target = payload.get("target_ip") or entity_key
        return (
            f"可达性 {target}: "
            f"{'可达' if payload.get('reachable') else '不可达/未知'}"
            + (f"；错误={payload.get('error')}" if payload.get("error") else "")
        )
    if state_type == "interface":
        status = str(payload.get("status") or payload.get("oper_status") or "").lower()
        admin = str(payload.get("admin_status") or "").lower()
        note = ""
        if "down" in admin:
            note = "（管理性关闭，可能是未使用端口）"
        elif "down" in status:
            note = "（链路/协议异常）"
        return (
            f"接口 {entity_key}: status={payload.get('status') or payload.get('oper_status')}, "
            f"admin={payload.get('admin_status')}, ip={payload.get('ip_address') or '未配置'}{note}"
        )
    if state_type == "route":
        return (
            f"路由 {entity_key}: protocol={payload.get('protocol')}, "
            f"next_hop={payload.get('next_hop') or '直连/无'}, "
            f"interface={payload.get('outgoing_interface') or '未知'}"
        )
    if state_type == "vlan":
        ports = payload.get("ports") or []
        return (
            f"VLAN {payload.get('vlan_id') or entity_key}: "
            f"name={payload.get('name')}, status={payload.get('status')}, "
            f"ports={','.join(str(port) for port in ports[:12]) or '无'}"
        )
    if state_type == "acl":
        rules = payload.get("rules") or []
        rendered = "; ".join(
            f"{rule.get('seq') or ''} {rule.get('action')} {rule.get('expression')}"
            for rule in rules[:8]
        )
        return f"ACL {entity_key}: {rendered or '无规则'}"
    if state_type == "neighbor":
        return (
            f"邻居 {entity_key}: {payload.get('local_interface')} -> "
            f"{payload.get('remote_device')}:{payload.get('remote_interface')} "
            f"({payload.get('protocol')})"
        )
    if state_type == "system":
        return (
            f"系统状态 {entity_key}: "
            f"{_short(json.dumps(payload, ensure_ascii=False), 260)}"
        )
    if state_type == "config":
        return (
            f"配置快照 {entity_key}: {payload.get('line_count')} 行, "
            f"sha256={_short(payload.get('sha256'), 16)}"
        )
    if state_type == "arp":
        return (
            f"ARP {entity_key}: mac={payload.get('mac')}, "
            f"interface={payload.get('interface')}"
        )
    return f"{state_type} {entity_key}: {_short(json.dumps(payload, ensure_ascii=False), 260)}"


def _select_observations(observations: list[dict]) -> list[dict]:
    """控制单设备提示词长度，同时优先保留故障定位相关状态。"""
    selected = []
    interface_count = 0
    for observation in sorted(
        observations,
        key=lambda item: (
            _STATE_PRIORITY.get(item.get("state_type"), 99),
            item.get("observed_at_utc") or "",
        ),
    ):
        if observation.get("state_type") == "interface":
            payload = observation.get("payload") or {}
            interesting = (
                payload.get("status") not in (None, "up")
                or payload.get("oper_status") not in (None, "up")
                or payload.get("ip_address")
            )
            if not interesting:
                continue
            interface_count += 1
            if interface_count > 30:
                continue
        selected.append(observation)
        if len(selected) >= MAX_OBSERVATIONS_PER_DEVICE:
            break
    return selected


def build_evidence_for_devices(device_names: list) -> list:
    evidence = []
    for name in device_names:
        bundle = db.get_current_device_bundle(name)
        device = bundle.get("device")
        result = bundle.get("result")
        if not device or not result:
            evidence.append(
                {
                    "device_name": name,
                    "collected_at": None,
                    "status": "no_data",
                    "command": None,
                    "summary": "该设备尚无已发布的采集结果，无法提供当前证据。",
                    "observation_id": None,
                    "run_id": None,
                    "priority": 9,
                }
            )
            continue

        observations = _select_observations(bundle.get("observations") or [])
        if not observations:
            if result.get("overall_status") == "skipped":
                summary = "该设备本轮未执行采集，当前状态未知。"
            elif result.get("reachability") == "unreachable":
                summary = (
                    "设备不可达，本轮未获得可用状态证据。"
                    f"错误: {result.get('error_message') or '未知'}"
                )
            else:
                summary = "本轮没有可解析的状态事实。"
            evidence.append(
                {
                    "device_name": name,
                    "collected_at": result.get("finished_at_utc") or result.get("started_at_utc"),
                    "status": "failed" if result.get("overall_status") == "failed" else "no_data",
                    "command": "device_reachability",
                    "summary": summary,
                    "observation_id": None,
                    "run_id": result.get("run_id"),
                    "priority": 0 if result.get("overall_status") == "failed" else 9,
                }
            )
            continue

        for observation in observations:
            evidence.append(
                {
                    "device_name": name,
                    "collected_at": observation.get("observed_at_utc"),
                    "status": "success",
                    "command": observation.get("command_or_tool") or observation.get("state_type"),
                    "summary": _summarize_observation(observation),
                    "observation_id": observation.get("observation_id"),
                    "item_id": observation.get("item_id"),
                    "run_id": observation.get("run_id") or result.get("run_id"),
                    "state_type": observation.get("state_type"),
                    "entity_key": observation.get("entity_key"),
                    "source": observation.get("source"),
                    "is_stale": _is_stale(observation.get("observed_at_utc")),
                    "priority": _observation_priority(observation)
                    + (20 if _is_stale(observation.get("observed_at_utc")) else 0),
                }
            )
    return select_compact_evidence(evidence)


def format_evidence_for_prompt(evidence: list) -> str:
    lines = []
    for item in evidence:
        stale_tag = "（注意：该证据超过30分钟，可能已过期）" if item.get("is_stale") else ""
        lines.append(
            f"- 设备: {item['device_name']} | 采集时间: {item['collected_at'] or '无'} | "
            f"状态: {item['status']} | 类型: {item.get('state_type') or item.get('command') or '无'} | "
            f"观测ID: {item.get('observation_id') or '无'}{stale_tag}\n"
            f"  {item['summary']}"
        )
    if not lines:
        return "（未提供任何设备证据）"
    return "\n".join(lines)
