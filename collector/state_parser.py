"""将设备原始输出解析为 ``state_observations``。

解析器只负责“原始输出 -> 状态事实”，不直接读写数据库。这样采集入口、
历史数据迁移和单元测试可以复用同一套解析逻辑。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_interface_name(name: str) -> str:
    """把 CDP/IOS 的短接口名统一成较完整的接口名。"""
    text = (name or "").strip()
    lower = text.lower()
    if lower.startswith(("ethernet", "gigabitethernet", "fastethernet", "serial")):
        return text
    if re.match(r"^Eth\s*", text, re.IGNORECASE):
        return re.sub(r"^Eth\s*", "Ethernet", text, flags=re.IGNORECASE)
    if re.match(r"^Et\s*", text, re.IGNORECASE):
        return re.sub(r"^Et\s*", "Ethernet", text, flags=re.IGNORECASE)
    if re.match(r"^Gig\s*", text, re.IGNORECASE):
        return re.sub(r"^Gig\s*", "GigabitEthernet", text, flags=re.IGNORECASE)
    if re.match(r"^Gi\s*", text, re.IGNORECASE):
        return re.sub(r"^Gi\s*", "GigabitEthernet", text, flags=re.IGNORECASE)
    if re.match(r"^Fa\s*", text, re.IGNORECASE):
        return re.sub(r"^Fa\s*", "FastEthernet", text, flags=re.IGNORECASE)
    if re.match(r"^Se\s*", text, re.IGNORECASE):
        return re.sub(r"^Se\s*", "Serial", text, flags=re.IGNORECASE)
    return text


def capture_type_for_command(command: str | None) -> str:
    cmd = (command or "").lower()
    if "interface brief" in cmd:
        return "interface"
    if "ip route" in cmd or "routing-table" in cmd:
        return "route"
    if "vlan brief" in cmd or "display vlan" in cmd:
        return "vlan"
    if "access-list" in cmd or "display acl" in cmd:
        return "acl"
    if "cdp neighbors" in cmd or "lldp neighbors" in cmd:
        return "neighbor"
    if "show arp" in cmd:
        return "arp"
    if "running-config" in cmd or "current-configuration" in cmd:
        return "config"
    if cmd.startswith("snmp:"):
        return "system"
    if cmd.startswith("ping"):
        return "ping"
    if cmd.startswith("ip addr") or cmd.startswith("ip route"):
        return "interface"
    return "command"


def _observation(
    state_type: str,
    entity_type: str,
    entity_key: str,
    payload: dict,
    confidence: str = "high",
) -> dict:
    return {
        "state_type": state_type,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "payload": payload,
        "confidence": confidence,
    }


_INTERFACE_BRIEF_RE = re.compile(
    r"^\s*(\S+)\s+(\S+)\s+\S+\s+\S+\s+"
    r"(administratively down|up|down|deleted)\s+(up|down)\s*$",
    re.IGNORECASE,
)


def parse_interface_brief(raw: str) -> list[dict]:
    observations = []
    for line in (raw or "").splitlines():
        match = _INTERFACE_BRIEF_RE.match(line)
        if not match:
            continue
        interface, address, status, protocol = match.groups()
        status_lower = status.lower()
        observations.append(
            _observation(
                "interface",
                "interface",
                normalize_interface_name(interface),
                {
                    "if_name": normalize_interface_name(interface),
                    "ip_address": None if address.lower() == "unassigned" else address,
                    "status": status_lower,
                    "protocol": protocol.lower(),
                    "admin_status": "down" if status_lower == "administratively down" else "up",
                    "oper_status": protocol.lower(),
                },
            )
        )
    return observations


def parse_snmp_interfaces(parsed: dict) -> list[dict]:
    observations = []
    for interface in parsed.get("interfaces", []) or []:
        if not isinstance(interface, dict):
            continue
        name = normalize_interface_name(str(interface.get("descr") or ""))
        if not name:
            continue
        observations.append(
            _observation(
                "interface",
                "interface",
                name,
                {
                    "if_name": name,
                    "if_index": interface.get("index"),
                    "admin_status": interface.get("admin_status"),
                    "oper_status": interface.get("oper_status"),
                    "source": "snmp",
                },
            )
        )
    return observations


_ROUTE_RE = re.compile(
    r"^\s*(?P<code>C|L|S\*?|O(?:\s+(?:IA|E1|E2))?|D(?:\s+EX)?|R|B|"
    r"i(?:\s+L1|\s+L2)?|ia|E1|E2|N1|N2)\s+"
    r"(?P<prefix>\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\s+(?P<rest>.+)$"
)
_METRIC_RE = re.compile(r"\[(\d+)/(\d+)\]")
_VIA_RE = re.compile(r"\bvia\s+([0-9.]+)")
_DIRECT_RE = re.compile(r"directly connected,\s*(\S+)?")


def parse_route_table(raw: str) -> list[dict]:
    observations = []
    for line in (raw or "").splitlines():
        match = _ROUTE_RE.match(line)
        if not match:
            continue
        code = re.sub(r"\s+", " ", match.group("code").strip())
        prefix = match.group("prefix")
        rest = match.group("rest").strip()
        metric = _METRIC_RE.search(rest)
        via = _VIA_RE.search(rest)
        direct = _DIRECT_RE.search(rest)
        observations.append(
            _observation(
                "route",
                "prefix",
                prefix,
                {
                    "prefix": prefix,
                    "protocol": code,
                    "next_hop": via.group(1) if via else None,
                    "outgoing_interface": direct.group(1) if direct else None,
                    "admin_distance": metric.group(1) if metric else None,
                    "metric": metric.group(2) if metric else None,
                    "raw": line.strip(),
                },
            )
        )
    return observations


_VLAN_RE = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(active|act/unsup|suspend|shutdown)\s*(.*)$",
    re.IGNORECASE,
)


def parse_vlan_brief(raw: str) -> list[dict]:
    observations = []
    for line in (raw or "").splitlines():
        match = _VLAN_RE.match(line)
        if not match:
            continue
        vlan_number, name, status, ports = match.groups()
        port_list = [item.strip() for item in ports.split(",") if item.strip()]
        observations.append(
            _observation(
                "vlan",
                "vlan",
                f"VLAN{vlan_number}",
                {
                    "vlan_id": int(vlan_number),
                    "name": name,
                    "status": status.lower(),
                    "ports": [normalize_interface_name(item) for item in port_list],
                },
            )
        )
    return observations


_ACL_HEADER_RE = re.compile(
    r"^\s*(?:(Extended|Standard|IPv6)\s+)?(?:IP\s+)?access list\s+(\S+)(?:\s+\(.*\))?\s*$",
    re.IGNORECASE,
)
_ACL_RULE_RE = re.compile(
    r"^\s*(\d+)?\s*(permit|deny)\s+(.+?)(?:\s+sequence\s+(\d+))?\s*$",
    re.IGNORECASE,
)


def parse_access_lists(raw: str) -> list[dict]:
    acl_data: dict[str, dict] = {}
    current = None
    for line in (raw or "").splitlines():
        header = _ACL_HEADER_RE.match(line)
        if header:
            acl_type, name = header.groups()
            current = acl_data.setdefault(
                name,
                {"name": name, "type": acl_type or "IP", "rules": []},
            )
            continue
        if current is None:
            continue
        rule = _ACL_RULE_RE.match(line)
        if not rule:
            continue
        seq, action, expression, explicit_seq = rule.groups()
        current["rules"].append(
            {
                "seq": int(seq or explicit_seq) if (seq or explicit_seq) else None,
                "action": action.lower(),
                "expression": expression.strip(),
            }
        )
    return [
        _observation("acl", "acl", name, data)
        for name, data in acl_data.items()
    ]


_CDP_RE = re.compile(
    r"^\s*(\S+)\s+((?:[A-Za-z]+)\s?\d+/\d+(?:\.\d+)?)\s+(\d+)\s+"
    r"(.+?)\s{2,}(.+?)\s+((?:[A-Za-z]+)\s?\d+/\d+(?:\.\d+)?)\s*$"
)


def parse_cdp_neighbors(raw: str) -> list[dict]:
    observations = []
    for line in (raw or "").splitlines():
        match = _CDP_RE.match(line)
        if not match:
            continue
        remote_device, local_interface, holdtime, capability, platform, remote_port = match.groups()
        local_interface = normalize_interface_name(local_interface)
        remote_port = normalize_interface_name(remote_port)
        observations.append(
            _observation(
                "neighbor",
                "neighbor",
                f"cdp:{local_interface}:{remote_device}",
                {
                    "protocol": "cdp",
                    "remote_device": remote_device,
                    "local_interface": local_interface,
                    "remote_interface": remote_port,
                    "holdtime": int(holdtime),
                    "capability": capability.strip(),
                    "platform": platform.strip(),
                },
            )
        )
    return observations


_ARP_RE = re.compile(r"^Internet\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def parse_arp_table(raw: str) -> list[dict]:
    observations = []
    for line in (raw or "").splitlines():
        match = _ARP_RE.match(line)
        if not match:
            continue
        address, age, mac, entry_type, interface = match.groups()
        observations.append(
            _observation(
                "arp",
                "arp",
                address,
                {
                    "ip_address": address,
                    "age_minutes": age,
                    "mac": mac,
                    "entry_type": entry_type,
                    "interface": normalize_interface_name(interface),
                },
            )
        )
    return observations


def parse_ping_result(raw: str, parsed: dict, target_ip: str | None = None) -> list[dict]:
    target = target_ip or parsed.get("target_ip")
    reachable = parsed.get("reachable")
    if reachable is None:
        text = raw or ""
        reachable = (
            " 0% packet loss" in text
            or " 0.0% packet loss" in text
            or "bytes from" in text.lower()
            or "reply from" in text.lower()
        )
    if not target:
        return []
    return [
        _observation(
            "reachability",
            "ip",
            str(target),
            {
                "target_ip": str(target),
                "reachable": bool(reachable),
                "raw_excerpt": (raw or "")[:500],
            },
        )
    ]


def parse_config(raw: str) -> list[dict]:
    text = raw or ""
    return [
        _observation(
            "config",
            "config",
            "running-config",
            {
                "line_count": len(text.splitlines()),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "excerpt": text[:500],
            },
        )
    ]


def parse_generic(command: str | None, raw: str, parsed: dict) -> list[dict]:
    return [
        _observation(
            "command_output",
            "command",
            command or "unknown",
            {
                "command": command,
                "raw_excerpt": (raw or "")[:800],
                "parsed": parsed,
            },
            confidence="medium",
        )
    ]


def observations_from_item(item: dict) -> list[dict]:
    """根据一条采集项生成状态事实列表。"""
    command = item.get("command_or_tool") or item.get("command")
    capture_type = item.get("capture_type") or capture_type_for_command(command)
    raw = item.get("raw_output") or ""
    parsed = _as_dict(item.get("parsed"))

    if capture_type == "interface":
        observations = parse_interface_brief(raw)
        if observations:
            return observations
        return parse_snmp_interfaces(parsed)
    if capture_type == "route":
        return parse_route_table(raw) or parse_generic(command, raw, parsed)
    if capture_type == "vlan":
        return parse_vlan_brief(raw) or parse_generic(command, raw, parsed)
    if capture_type == "acl":
        return parse_access_lists(raw) or parse_generic(command, raw, parsed)
    if capture_type == "neighbor":
        return parse_cdp_neighbors(raw) or parse_generic(command, raw, parsed)
    if capture_type == "arp":
        return parse_arp_table(raw) or parse_generic(command, raw, parsed)
    if capture_type == "config":
        return parse_config(raw)
    if capture_type == "ping":
        return parse_ping_result(raw, parsed, parsed.get("target_ip"))
    if capture_type == "system":
        observations = []
        if parsed.get("sys_uptime") is not None:
            observations.append(
                _observation(
                    "system",
                    "device",
                    "sysUpTime",
                    {
                        "sys_uptime": parsed.get("sys_uptime"),
                        "online": True,
                    },
                )
            )
        observations.extend(parse_snmp_interfaces(parsed))
        return observations or parse_generic(command, raw, parsed)
    return parse_generic(command, raw, parsed)
