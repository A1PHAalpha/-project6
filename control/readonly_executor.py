"""
只读检查执行器：供 Web 界面"立即执行只读检查"按钮调用。
严格限制在 whitelist_commands.py 定义的命令范围内，拒绝执行任何未在白名单中的命令。
这是本系统唯一允许自动执行的设备操作入口；任何配置变更都不会经过此模块。
"""
import logging

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from collector import db
from collector.whitelist_commands import is_readonly_command

logger = logging.getLogger("readonly_executor")


class CommandNotWhitelistedError(Exception):
    pass


def execute_readonly(device: dict, command: str, username: str, password: str, timeout: int = 10) -> dict:
    """
    执行单条只读命令。若命令不在白名单内，直接拒绝并抛出异常，不发往设备。
    """
    device_type = device["device_type"]
    if not is_readonly_command(device_type, command):
        logger.error("拒绝执行非白名单命令: device=%s command=%s", device["device_name"], command)
        raise CommandNotWhitelistedError(f"命令 '{command}' 不在只读白名单内，已拒绝执行")

    conn_params = {
        "device_type": device_type,
        "host": device["mgmt_ip"],
        "username": username,
        "password": password,
        "port": device.get("ssh_port", 22),
        "timeout": timeout,
    }

    try:
        conn = ConnectHandler(**conn_params)
    except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
        reason = f"连接失败: {type(e).__name__}: {e}"
        db.insert_collection_record(device["device_name"], status="failed", command=command, fail_reason=reason)
        return {"status": "failed", "reason": reason}

    try:
        raw = conn.send_command(command, read_timeout=timeout)
        db.insert_collection_record(device["device_name"], status="success", command=command, raw_output=raw)
        return {"status": "success", "raw_output": raw}
    except Exception as e:
        reason = f"命令执行失败: {type(e).__name__}: {e}"
        db.insert_collection_record(device["device_name"], status="failed", command=command, fail_reason=reason)
        return {"status": "failed", "reason": reason}
    finally:
        conn.disconnect()
