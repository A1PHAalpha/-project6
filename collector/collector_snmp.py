"""SNMP 采集模块：返回在线状态和接口状态，不直接写数据库。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
    nextCmd,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("collector_snmp")

OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

STATUS_MAP = {1: "up", 2: "down", 3: "testing"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snmp_get(ip: str, community: str, oid: str, port: int = 161, timeout: int = 5):
    iterator = getCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=1),
        UdpTransportTarget((ip, port), timeout=timeout, retries=1),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
    )
    error_indication, error_status, error_index, var_binds = next(iterator)
    if error_indication:
        raise ConnectionError(str(error_indication))
    if error_status:
        raise ConnectionError(f"{error_status.prettyPrint()} at {error_index}")
    return str(var_binds[0][1])


def snmp_walk_interfaces(ip: str, community: str, port: int = 161, timeout: int = 5):
    """遍历接口表，返回 [{index, descr, admin_status, oper_status}]。"""
    descr_map, admin_map, oper_map = {}, {}, {}

    def _walk(oid, target_map, transform=str):
        for (error_indication, error_status, error_index, var_binds) in nextCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            UdpTransportTarget((ip, port), timeout=timeout, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        ):
            if error_indication:
                raise ConnectionError(str(error_indication))
            if error_status:
                raise ConnectionError(f"{error_status.prettyPrint()}")
            for var_bind in var_binds:
                oid_str, value = var_bind
                idx = str(oid_str).split(".")[-1]
                target_map[idx] = transform(value)

    _walk(OID_IF_DESCR, descr_map, str)
    _walk(OID_IF_ADMIN_STATUS, admin_map, lambda value: STATUS_MAP.get(int(value), str(value)))
    _walk(OID_IF_OPER_STATUS, oper_map, lambda value: STATUS_MAP.get(int(value), str(value)))

    interfaces = []
    for idx in descr_map:
        interfaces.append(
            {
                "index": idx,
                "descr": descr_map.get(idx),
                "admin_status": admin_map.get(idx),
                "oper_status": oper_map.get(idx),
            }
        )
    return interfaces


def collect_device_snmp(device: dict, community: str) -> dict:
    """采集一台设备的 SNMP 在线状态和接口状态。"""
    device_name = device["device_name"]
    ip = device["mgmt_ip"]
    port = device.get("snmp_port") or 161
    started = _utc_now()
    try:
        uptime = snmp_get(ip, community, OID_SYS_UPTIME, port=port)
        interfaces = snmp_walk_interfaces(ip, community, port=port)
        parsed = {"sys_uptime": uptime, "interfaces": interfaces}
        logger.info("[%s] SNMP 采集成功，在线，接口数=%d", device_name, len(interfaces))
        return {
            "status": "success",
            "source": "snmp",
            "capture_type": "system",
            "command": "SNMP:ifTable+sysUpTime",
            "started_at": started,
            "finished_at": _utc_now(),
            "raw_output": str(parsed),
            "parsed": parsed,
        }
    except Exception as exc:
        reason = f"SNMP采集失败: {type(exc).__name__}: {exc}"
        logger.warning("[%s] %s", device_name, reason)
        return {
            "status": "failed",
            "source": "snmp",
            "capture_type": "system",
            "command": "SNMP:ifTable+sysUpTime",
            "started_at": started,
            "finished_at": _utc_now(),
            "error_code": "snmp_failed",
            "fail_reason": reason,
        }


def collect_all_snmp(devices: list, community: str) -> list[dict]:
    summary = []
    for device in devices:
        if device.get("device_type") == "linux" or not device.get("snmp_port"):
            continue
        summary.append(
            {
                "device_name": device["device_name"],
                "result": collect_device_snmp(device, community),
            }
        )
    return summary
