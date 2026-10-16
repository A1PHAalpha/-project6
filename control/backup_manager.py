"""
配置备份管理：在任何人工确认的配置变更操作之前，必须先备份当前运行配置。
备份文件存放于 reports/backups/，文件名含设备名和时间戳，供事后审计和回滚参考。
"""
import logging
import os
from datetime import datetime
from pathlib import Path

from netmiko import ConnectHandler

logger = logging.getLogger("backup_manager")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = str(PROJECT_ROOT / "reports" / "backups")


def backup_running_config(device: dict, username: str, password: str, timeout: int = 10) -> dict:
    """
    获取设备当前运行配置并保存为本地文件。
    返回 {status, backup_path, reason}。
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    device_type = device["device_type"]
    cmd_map = {"cisco_ios": "show running-config", "huawei_vrp": "display current-configuration"}
    command = cmd_map.get(device_type)
    if not command:
        return {"status": "failed", "reason": f"设备类型 {device_type} 不支持配置备份"}

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
        raw = conn.send_command(command, read_timeout=timeout)
        conn.disconnect()
    except Exception as e:
        reason = f"备份失败: {type(e).__name__}: {e}"
        logger.error(reason)
        return {"status": "failed", "reason": reason}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{device['device_name']}_{timestamp}.cfg"
    path = os.path.join(BACKUP_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw)
    # 备份含设备明文配置，收紧权限到仅属主可读写
    os.chmod(path, 0o600)

    logger.info("已备份 %s 配置到 %s", device["device_name"], path)
    return {"status": "success", "backup_path": path}
