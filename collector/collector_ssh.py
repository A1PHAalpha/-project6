"""
SSH 采集模块：通过 netmiko 对路由器/交换机执行白名单只读命令。
采集接口状态/VLAN/路由表/关键配置，脱敏后写入 SQLite。

安全要点：
- 口令由 netmiko 通过 SSH 协议传递，不再出现在命令行/``ps`` 输出中。
- 写库前调用 sanitize_text()，库内不保留明文凭据。
"""
import json
import logging
import os

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException

from rag_engine.sanitize import sanitize_text

from . import db
from .whitelist_commands import READONLY_COMMANDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("collector_ssh")

# 老 Cisco 设备兼容开关，默认关闭。开启后强制使用 ssh-rsa 主机密钥算法。
SSH_LEGACY_ALGOS = os.getenv("SSH_LEGACY_ALGOS", "false").lower() in ("1", "true", "yes")


def _connect_params(host: str, username: str, password: str, device_type: str, timeout: int) -> dict:
    params = {
        "device_type": device_type,
        "host": host,
        "username": username,
        "password": password,
        "timeout": timeout,
    }
    if SSH_LEGACY_ALGOS:
        # paramiko 默认优先 rsa-sha2-*；老设备只支持 ssh-rsa，需要禁用新算法强制回退。
        params["disabled_algorithms"] = {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
    return params


def execute_ssh_command(host: str, username: str, password: str, command: str,
                        timeout: int = 15, device_type: str = "cisco_ios") -> str:
    """
    通过 netmiko 建立 SSH 连接并执行单条命令，返回命令输出。
    连接超时统一抛出 TimeoutError，便于调用方区分。
    """
    try:
        conn = ConnectHandler(**_connect_params(host, username, password, device_type, timeout))
    except NetmikoTimeoutException as e:
        raise TimeoutError(f"Command timeout after {timeout}s") from e

    try:
        return conn.send_command(command, read_timeout=timeout)
    finally:
        conn.disconnect()


def collect_device(device: dict, username: str, password: str, timeout: int = 15):
    """
    对单台设备执行一轮采集。
    """
    device_name = device["device_name"]
    device_type = device["device_type"]
    commands = READONLY_COMMANDS.get(device_type, {})
    results = []

    if device_type == "linux":
        logger.info("[%s] 跳过Linux设备（暂无SSH采集）", device_name)
        return results

    host = device["mgmt_ip"]

    for item, cmd in commands.items():
        if cmd is None:
            continue

        logger.info("[%s] 执行命令: %s", device_name, cmd)

        try:
            output = execute_ssh_command(host, username, password, cmd, timeout, device_type=device_type)

            # 入库前脱敏：库内不保留明文凭据
            safe_output = sanitize_text(output)
            parsed = json.dumps({"raw": safe_output}, ensure_ascii=False)

            db.insert_collection_record(
                device_name,
                status="success",
                command=cmd,
                raw_output=safe_output,
                parsed=parsed,
            )
            results.append({
                "item": item,
                "command": cmd,
                "status": "success",
                "output_lines": len(output.split('\n'))
            })
            logger.info("[%s] %s 成功", device_name, item)

        except TimeoutError as e:
            reason = str(e)
            logger.warning("[%s] %s: %s", device_name, item, reason)
            db.insert_collection_record(
                device_name,
                status="failed",
                command=cmd,
                fail_reason=reason,
            )
            results.append({"item": item, "command": cmd, "status": "failed", "reason": reason})

        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            logger.error("[%s] %s: %s", device_name, item, reason)
            db.insert_collection_record(
                device_name,
                status="failed",
                command=cmd,
                fail_reason=reason,
            )
            results.append({"item": item, "command": cmd, "status": "failed", "reason": reason})

    if results:
        success_count = sum(1 for r in results if r["status"] == "success")
        logger.info("[%s] 采集完成: %d/%d 成功", device_name, success_count, len(results))

    return results


def collect_all(devices: list, username: str, password: str):
    """
    采集所有设备，返回每台设备的采集结果摘要列表。
    """
    logger.info("开始全局采集，共 %d 台设备", len(devices))

    summary = []
    for device in devices:
        device_name = device["device_name"]
        logger.info("开始采集设备: %s (%s)", device_name, device["mgmt_ip"])

        results = collect_device(device, username, password)

        if not results:
            logger.warning("[%s] 该设备无采集结果", device_name)

        summary.append({"device_name": device_name, "results": results})

    logger.info("全局采集完成")
    return summary
