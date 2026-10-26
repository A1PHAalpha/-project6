"""SSH 采集模块。

采集器只返回结果，不直接写数据库；由 ``run_collect`` 统一创建采集批次、
设备结果和状态事实。这样批量采集与 LLM 单设备工具调用可以复用同一套落库逻辑。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from rag_engine.sanitize import sanitize_text

from .whitelist_commands import get_collection_commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("collector_ssh")

SSH_LEGACY_ALGOS = os.getenv("SSH_LEGACY_ALGOS", "false").lower() in ("1", "true", "yes")
CAPTURE_TYPES = {
    "interface_status": "interface",
    "vlan_brief": "vlan",
    "route_table": "route",
    "running_config": "config",
    "cdp_neighbors": "neighbor",
    "access_list": "acl",
    "arp_table": "arp",
}

_INVALID_OUTPUT_MARKERS = (
    "% invalid input",
    "% incomplete command",
    "% ambiguous command",
    "% unrecognized command",
)


def is_invalid_command_output(output: str) -> bool:
    """Detect Cisco CLI error markers that Netmiko returns as normal text."""
    text = str(output or "").lower()
    return any(marker in text for marker in _INVALID_OUTPUT_MARKERS)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect_params(host: str, username: str, password: str, device_type: str, timeout: int) -> dict:
    params = {
        "device_type": device_type,
        "host": host,
        "username": username,
        "password": password,
        "timeout": timeout,
    }
    if SSH_LEGACY_ALGOS:
        params["disabled_algorithms"] = {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
    return params


def _connect_attempts() -> int:
    try:
        retries = int(os.getenv("SSH_CONNECT_RETRIES", "1"))
    except (TypeError, ValueError):
        retries = 1
    return max(0, min(retries, 5)) + 1


def _retry_delay_seconds() -> float:
    try:
        delay = float(os.getenv("SSH_RETRY_DELAY_SECONDS", "1.0"))
    except (TypeError, ValueError):
        delay = 1.0
    return max(0.0, min(delay, 10.0))


def _connect_with_retry(params: dict, *, device_name: str = "device"):
    attempts = _connect_attempts()
    delay = _retry_delay_seconds()
    last_exception = None
    for attempt in range(1, attempts + 1):
        try:
            return ConnectHandler(**params)
        except NetmikoAuthenticationException:
            # Bad credentials are not transient; do not risk account lockout.
            raise
        except Exception as exc:
            last_exception = exc
            if attempt >= attempts:
                raise
            logger.warning(
                "[%s] SSH 连接失败（第 %d/%d 次），%.1f 秒后重试: %s",
                device_name,
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    raise last_exception


def execute_ssh_command(
    host: str,
    username: str,
    password: str,
    command: str,
    timeout: int = 15,
    device_type: str = "cisco_ios",
) -> str:
    """执行单条 SSH 命令，供独立只读检查复用。"""
    try:
        conn = _connect_with_retry(
            _connect_params(host, username, password, device_type, timeout),
            device_name=host,
        )
    except NetmikoTimeoutException as exc:
        raise TimeoutError(f"Command timeout after {timeout}s") from exc
    try:
        return conn.send_command(command, read_timeout=timeout)
    finally:
        conn.disconnect()


def collect_device(device: dict, username: str, password: str, timeout: int = 15) -> list[dict]:
    """对单台设备执行一轮只读 SSH 采集。

    同一台设备只建立一个 SSH 会话，避免每条命令重新登录。命令失败会作为
    ``collection_item`` 返回，由上层决定设备级 reachability 和 overall_status。
    """
    device_name = device["device_name"]
    device_type = device["device_type"]
    commands = get_collection_commands(device)
    if device_type == "linux":
        logger.info("[%s] Linux 设备不走网络设备 SSH 命令集", device_name)
        return []
    if not commands:
        return [
            {
                "item": "unsupported_device_type",
                "command": None,
                "status": "failed",
                "source": "ssh",
                "capture_type": "command",
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "error_code": "unsupported_device_type",
                "fail_reason": f"不支持的设备类型: {device_type}",
            }
        ]

    host = device["mgmt_ip"]
    started = _utc_now()
    try:
        conn = _connect_with_retry(
            _connect_params(host, username, password, device_type, timeout),
            device_name=device_name,
        )
    except Exception as exc:
        reason = sanitize_text(f"{type(exc).__name__}: {exc}")
        logger.warning("[%s] SSH 连接失败: %s", device_name, reason)
        return [
            {
                "item": "ssh_connection",
                "command": None,
                "status": "failed",
                "source": "ssh",
                "capture_type": "command",
                "started_at": started,
                "finished_at": _utc_now(),
                "error_code": "ssh_connection_failed",
                "fail_reason": reason,
            }
        ]

    results = []
    try:
        for item, command in commands.items():
            if command is None:
                continue
            command_started = _utc_now()
            logger.info("[%s] 执行命令: %s", device_name, command)
            try:
                output = conn.send_command(command, read_timeout=timeout)
                safe_output = sanitize_text(output)
                if is_invalid_command_output(safe_output):
                    logger.warning(
                        "[%s] %s 返回命令错误提示", device_name, item
                    )
                    results.append(
                        {
                            "item": item,
                            "command": command,
                            "status": "failed",
                            "source": "ssh",
                            "capture_type": CAPTURE_TYPES.get(item, "command"),
                            "started_at": command_started,
                            "finished_at": _utc_now(),
                            "error_code": "invalid_command_output",
                            "fail_reason": "设备返回命令错误提示",
                            "raw_output": safe_output,
                            "parsed": {
                                "line_count": len(safe_output.splitlines())
                            },
                        }
                    )
                    continue
                results.append(
                    {
                        "item": item,
                        "command": command,
                        "status": "success",
                        "source": "ssh",
                        "capture_type": CAPTURE_TYPES.get(item, "command"),
                        "started_at": command_started,
                        "finished_at": _utc_now(),
                        "raw_output": safe_output,
                        "parsed": {"line_count": len(safe_output.splitlines())},
                    }
                )
            except Exception as exc:
                reason = sanitize_text(f"{type(exc).__name__}: {exc}")
                logger.warning("[%s] %s 失败: %s", device_name, item, reason)
                results.append(
                    {
                        "item": item,
                        "command": command,
                        "status": "failed",
                        "source": "ssh",
                        "capture_type": CAPTURE_TYPES.get(item, "command"),
                        "started_at": command_started,
                        "finished_at": _utc_now(),
                        "error_code": "ssh_command_failed",
                        "fail_reason": reason,
                    }
                )
    finally:
        conn.disconnect()
    logger.info(
        "[%s] SSH 采集完成: %d/%d 成功",
        device_name,
        sum(1 for result in results if result["status"] == "success"),
        len(results),
    )
    return results


def collect_all(devices: list, username: str, password: str) -> list[dict]:
    """采集多台设备，返回结果摘要，不写数据库。"""
    summary = []
    for device in devices:
        results = collect_device(device, username, password)
        summary.append({"device_name": device["device_name"], "results": results})
    return summary
