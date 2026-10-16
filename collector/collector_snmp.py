"""
SNMP 采集模块：轮询设备在线状态与接口状态（ifOperStatus/ifAdminStatus）。
作为 SSH 采集的补充证据来源，尤其在 SSH 不可达时仍可能通过 SNMP 确认设备存活。
"""
import logging

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

from . import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("collector_snmp")

# 标准 MIB-2 接口表 OID
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

STATUS_MAP = {1: "up", 2: "down", 3: "testing"}


def snmp_get(ip: str, community: str, oid: str, port: int = 161, timeout: int = 5):
    iterator = getCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=1),  # SNMPv2c
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
    """遍历接口表，返回 [{index, descr, admin_status, oper_status}]"""
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
    _walk(OID_IF_ADMIN_STATUS, admin_map, lambda v: STATUS_MAP.get(int(v), str(v)))
    _walk(OID_IF_OPER_STATUS, oper_map, lambda v: STATUS_MAP.get(int(v), str(v)))

    interfaces = []
    for idx in descr_map:
        interfaces.append({
            "index": idx,
            "descr": descr_map.get(idx),
            "admin_status": admin_map.get(idx),
            "oper_status": oper_map.get(idx),
        })
    return interfaces


def collect_device_snmp(device: dict, community: str):
    """对单台设备做 SNMP 在线状态 + 接口状态采集，写入 collection_records。"""
    device_name = device["device_name"]
    ip = device["mgmt_ip"]
    port = device.get("snmp_port") or 161

    try:
        uptime = snmp_get(ip, community, OID_SYS_UPTIME, port=port)
        interfaces = snmp_walk_interfaces(ip, community, port=port)
        parsed = {"sys_uptime": uptime, "interfaces": interfaces}
        db.insert_collection_record(
            device_name, status="success", command="SNMP:ifTable+sysUpTime",
            raw_output=str(parsed), parsed=parsed,
        )
        logger.info("[%s] SNMP采集成功，在线，接口数=%d", device_name, len(interfaces))
        return {"status": "success", "parsed": parsed}
    except Exception as e:
        reason = f"SNMP采集失败: {type(e).__name__}: {e}"
        db.insert_collection_record(
            device_name, status="failed", command="SNMP:ifTable+sysUpTime", fail_reason=reason,
        )
        logger.warning("[%s] %s", device_name, reason)
        return {"status": "failed", "reason": reason}


def collect_all_snmp(devices: list, community: str):
    summary = []
    for device in devices:
        if device["device_type"] == "linux" or not device.get("snmp_port"):
            continue
        summary.append({
            "device_name": device["device_name"],
            "result": collect_device_snmp(device, community),
        })
    return summary
