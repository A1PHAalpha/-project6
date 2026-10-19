"""
采集任务入口脚本。可手动运行，也可配合定时任务(cron/Windows任务计划)周期执行。

用法:
    python -m collector.run_collect                  # 全量：SSH + SNMP + 连通性复测
    python -m collector.run_collect --devices A,B     # 仅对指定设备做 SSH 采集
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from project_config import load_project_env

load_project_env()

from collector import db
from collector.collector_snmp import collect_all_snmp
from collector.collector_ssh import collect_all
from collector.connectivity_test import record_connectivity_test

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_collect")

INVENTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "network", "device_inventory.json")


class CollectionConfigurationError(RuntimeError):
    """Raised when collection cannot start because configuration is incomplete."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="设备状态采集入口")
    parser.add_argument("--devices", help="仅采集指定设备（逗号分隔），不填则采集全部设备")
    return parser.parse_args(argv)


def run_collection(devices_arg: str | None = None) -> dict:
    """Run one collection pass and return a machine-readable summary."""
    db.init_db()
    devices = db.load_inventory(INVENTORY_PATH)
    logger.info("已加载设备台账，共 %d 台设备", len(devices))

    username = os.getenv("DEVICE_USERNAME")
    password = os.getenv("DEVICE_PASSWORD")
    community = os.getenv("SNMP_COMMUNITY", "public")

    if not username or not password:
        raise CollectionConfigurationError(
            "未配置 DEVICE_USERNAME / DEVICE_PASSWORD，无法执行设备采集"
        )

    if devices_arg:
        names = [n.strip() for n in devices_arg.split(",") if n.strip()]
        by_name = {d["device_name"]: d for d in devices}
        missing = [n for n in names if n not in by_name]
        if missing:
            logger.warning("未在台账中找到设备: %s", ", ".join(missing))
        selected = [by_name[n] for n in names if n in by_name]
        if not selected:
            raise CollectionConfigurationError("未找到任何指定的设备")
        logger.info("===== 开始 SSH 采集（指定 %d 台设备）=====", len(selected))
        ssh_summary = collect_all(selected, username, password)
        success = sum(1 for item in ssh_summary for result in item["results"] if result["status"] == "success")
        failed = sum(1 for item in ssh_summary for result in item["results"] if result["status"] == "failed")
        logger.info("指定设备采集完成")
        return {
            "mode": "selected",
            "device_count": len(selected),
            "requested_devices": names,
            "missing_devices": missing,
            "ssh_commands_success": success,
            "ssh_commands_failed": failed,
            "snmp_devices_success": 0,
            "snmp_devices_failed": 0,
            "connectivity_tests": 0,
        }

    logger.info("===== 开始 SSH 采集 =====")
    ssh_summary = collect_all(devices, username, password)

    logger.info("===== 开始 SNMP 采集 =====")
    snmp_summary = collect_all_snmp(devices, community)

    logger.info("===== 开始连通性复测（分支终端 -> 总部服务器）=====")
    hq_server = next((d for d in devices if d["device_name"] == "HQ-SRV"), None)
    branch_pcs = [d for d in devices if d["device_type"] == "linux" and d["device_name"] != "HQ-SRV"]
    connectivity_tests = 0
    if hq_server:
        for pc in branch_pcs:
            record_connectivity_test(
                pc["device_name"], hq_server["mgmt_ip"],
                purpose=f"{pc['device_name']} 到总部服务器连通性测试",
            )
            connectivity_tests += 1

    success = sum(1 for s in ssh_summary for r in s["results"] if r["status"] == "success")
    failed = sum(1 for s in ssh_summary for r in s["results"] if r["status"] == "failed")
    snmp_success = sum(1 for item in snmp_summary if item["result"]["status"] == "success")
    snmp_failed = len(snmp_summary) - snmp_success
    logger.info("采集完成: SSH成功=%d 失败=%d, SNMP设备数=%d", success, failed, len(snmp_summary))
    return {
        "mode": "full",
        "device_count": len(devices),
        "requested_devices": None,
        "missing_devices": [],
        "ssh_commands_success": success,
        "ssh_commands_failed": failed,
        "snmp_devices_success": snmp_success,
        "snmp_devices_failed": snmp_failed,
        "connectivity_tests": connectivity_tests,
    }


def main(argv=None):
    args = parse_args(argv)
    try:
        result = run_collection(args.devices)
    except CollectionConfigurationError as exc:
        logger.error("%s", exc)
        return 2
    logger.info("采集摘要: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
