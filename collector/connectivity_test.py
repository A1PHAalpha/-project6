"""
终端连通性测试：通过 ping 验证业务可达性（如"分支终端能否访问总部服务器"）。
用于初始建网验证，也用于故障修复后的复测环节。
"""
import logging
import platform
import subprocess

from . import db

logger = logging.getLogger("connectivity_test")


def ping(target_ip: str, count: int = 4, timeout: int = 2) -> dict:
    """
    执行 ping 命令，返回 {success, loss_rate, raw_output}。
    跨平台兼容 Windows / Linux。
    """
    is_windows = platform.system().lower() == "windows"
    if is_windows:
        cmd = ["ping", "-n", str(count), "-w", str(timeout * 1000), target_ip]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout), target_ip]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * count + 5)
        output = proc.stdout
        success = proc.returncode == 0
        return {"success": success, "raw_output": output, "target_ip": target_ip}
    except Exception as e:
        return {"success": False, "raw_output": f"ping执行异常: {e}", "target_ip": target_ip}


def record_connectivity_test(source_device_name: str, target_ip: str, purpose: str = ""):
    """执行 ping 并写入采集记录，供诊断引擎和复测环节引用。"""
    result = ping(target_ip)
    status = "success" if result["success"] else "failed"
    db.insert_collection_record(
        source_device_name,
        status=status,
        command=f"ping {target_ip}",
        raw_output=result["raw_output"],
        parsed={"purpose": purpose, "target_ip": target_ip, "reachable": result["success"]},
        fail_reason=None if result["success"] else f"目标 {target_ip} 不可达",
    )
    return result
