"""终端连通性测试。

批量采集会优先在源终端上执行 ping；只有人工复测兼容入口仍保留从运维主机
执行的方式，并在 parsed 中明确标注 execution_location。
"""
from __future__ import annotations

import ipaddress
import logging
import platform
import re
import subprocess
from datetime import datetime, timezone

from . import db

logger = logging.getLogger("connectivity_test")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loss_rate(output: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet\s*)?loss", output or "", re.IGNORECASE)
    return float(match.group(1)) if match else None


def ping(target_ip: str, count: int = 4, timeout: int = 2) -> dict:
    """从当前运维主机执行 ping。"""
    is_windows = platform.system().lower() == "windows"
    if is_windows:
        command = ["ping", "-n", str(count), "-w", str(timeout * 1000), target_ip]
    else:
        command = ["ping", "-c", str(count), "-W", str(timeout), target_ip]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout * count + 5,
        )
        output = proc.stdout
        return {
            "success": proc.returncode == 0,
            "loss_rate": _loss_rate(output),
            "raw_output": output,
            "target_ip": target_ip,
        }
    except Exception as exc:
        return {
            "success": False,
            "loss_rate": None,
            "raw_output": f"ping执行异常: {exc}",
            "target_ip": target_ip,
        }


def collect_router_ping(
    source_device: dict,
    target_ip: str,
    username: str,
    password: str,
    timeout: int = 20,
) -> dict:
    """从可 SSH 的路由器执行 ping，适用于没有 SSH 的 VPCS 终端。"""
    from .collector_ssh import execute_ssh_command

    started = _utc_now()
    command = f"ping {target_ip} repeat 4"
    try:
        output = execute_ssh_command(
            source_device["mgmt_ip"],
            username,
            password,
            command,
            timeout=timeout,
            device_type=source_device.get("device_type") or "cisco_ios",
        )
        match = re.search(r"Success rate is (\d+) percent", output, re.IGNORECASE)
        success_rate = int(match.group(1)) if match else None
        reachable = bool(
            (success_rate is not None and success_rate > 0)
            or re.search(r"!{2,}", output)
        )
        return {
            "status": "success",
            "source": "ping",
            "capture_type": "ping",
            "command": command,
            "started_at": started,
            "finished_at": _utc_now(),
            "raw_output": output,
            "reachable": reachable,
            "parsed": {
                "target_ip": target_ip,
                "reachable": reachable,
                "success_rate": success_rate,
                "source_device": source_device["device_name"],
                "execution_location": "source_router",
            },
            "observation_status": "observed" if reachable else "unreachable",
        }
    except Exception as exc:
        reason = f"源路由器 ping 失败: {type(exc).__name__}: {exc}"
        return {
            "status": "failed",
            "source": "ping",
            "capture_type": "ping",
            "command": command,
            "started_at": started,
            "finished_at": _utc_now(),
            "reachable": None,
            "parsed": {
                "target_ip": target_ip,
                "reachable": None,
                "source_device": source_device["device_name"],
                "execution_location": "source_router",
            },
            "error_code": "source_ping_failed",
            "fail_reason": reason,
            "observation_status": "unknown",
        }


def collect_terminal_ping(
    device: dict,
    target_ip: str,
    username: str,
    password: str,
    timeout: int = 10,
) -> dict:
    """通过 SSH 在源终端上执行 ping，避免把运维主机结果冒充分支终端结果。"""
    from .collector_ssh import execute_ssh_command

    started = _utc_now()
    try:
        ipaddress.ip_address(target_ip)
    except ValueError:
        return {
            "status": "failed",
            "source": "ping",
            "capture_type": "ping",
            "command": f"ping {target_ip}",
            "started_at": started,
            "finished_at": _utc_now(),
            "error_code": "invalid_target_ip",
            "fail_reason": f"目标地址不合法: {target_ip}",
        }

    command = f"ping -c 4 -W 2 {target_ip}"
    try:
        output = execute_ssh_command(
            device["mgmt_ip"],
            username,
            password,
            command,
            timeout=timeout,
            device_type=device.get("device_type") or "linux",
        )
        reachable = bool(
            re.search(r"\b0(?:\.0)?%\s*(?:packet\s*)?loss", output, re.IGNORECASE)
            or re.search(r"\bbytes from\b", output, re.IGNORECASE)
            or re.search(r"\breply from\b", output, re.IGNORECASE)
        )
        return {
            "status": "success",
            "source": "ping",
            "capture_type": "ping",
            "command": command,
            "started_at": started,
            "finished_at": _utc_now(),
            "raw_output": output,
            "reachable": reachable,
            "parsed": {
                "target_ip": target_ip,
                "reachable": reachable,
                "source_device": device["device_name"],
                "execution_location": "source_device",
            },
            "observation_status": "observed" if reachable else "unreachable",
        }
    except Exception as exc:
        reason = f"源终端 ping 失败: {type(exc).__name__}: {exc}"
        return {
            "status": "failed",
            "source": "ping",
            "capture_type": "ping",
            "command": command,
            "started_at": started,
            "finished_at": _utc_now(),
            "reachable": None,
            "parsed": {
                "target_ip": target_ip,
                "reachable": None,
                "source_device": device["device_name"],
                "execution_location": "source_device",
            },
            "error_code": "source_ping_failed",
            "fail_reason": reason,
            "observation_status": "unknown",
        }


def record_connectivity_test(source_device_name: str, target_ip: str, purpose: str = ""):
    """兼容人工复测入口：执行 ping 并写入一条 recheck 采集批次。"""
    result = ping(target_ip)
    status = "success" if result["success"] else "failed"
    db.insert_collection_record(
        source_device_name,
        status=status,
        command=f"ping {target_ip}",
        raw_output=result["raw_output"],
        parsed={
            "purpose": purpose,
            "target_ip": target_ip,
            "reachable": result["success"],
            "execution_location": "collector_host",
        },
        fail_reason=None if result["success"] else f"目标 {target_ip} 不可达",
        source="ping",
        trigger_type="recheck",
        result_type="recheck",
    )
    return result
