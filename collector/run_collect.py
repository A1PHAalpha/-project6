"""采集任务入口：创建采集批次、设备结果、采集项和状态事实。

用法：
    python -m collector.run_collect
    python -m collector.run_collect --devices BR-A-R2,BR-A-SW2

批量采集、指定设备采集以及未来的 LLM/MCP 单设备工具调用都使用同一套
``collection_runs -> device_capture_results -> collection_items`` 模型。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from project_config import load_project_env

load_project_env()

from collector import db
from collector.collector_snmp import collect_device_snmp
from collector.collector_ssh import collect_device
from collector.connectivity_test import collect_router_ping, collect_terminal_ping

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_collect")

INVENTORY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "network", "device_inventory.json"
)


class CollectionConfigurationError(RuntimeError):
    """采集前置配置不完整。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="设备状态采集入口")
    parser.add_argument("--devices", help="仅采集指定设备（逗号分隔），不填则采集全部设备")
    parser.add_argument(
        "--trigger",
        default="manual",
        choices=["manual", "startup", "scheduled", "llm_tool", "mcp"],
        help="采集触发类型",
    )
    parser.add_argument("--requested-by", default="cli", help="发起人或系统")
    parser.add_argument("--source-ref", default=None, help="工具调用 ID 或外部任务 ID")
    return parser.parse_args(argv)


def _result_ok(result: dict) -> bool:
    if result.get("status") != "success":
        return False
    parsed = result.get("parsed") or {}
    if result.get("capture_type") == "ping" and parsed.get("reachable") is False:
        return False
    return True


def _status_counts(results: list[dict]) -> tuple[int, int]:
    success = sum(1 for item in results if _result_ok(item))
    failed = sum(1 for item in results if not _result_ok(item))
    return success, failed


def _device_overall(results: list[dict]) -> tuple[str, str, str | None]:
    if not results:
        return "skipped", "unknown", None
    success, failed = _status_counts(results)
    reachability = "reachable" if success else "unreachable"
    if failed == 0:
        overall = "success"
    elif success:
        overall = "partial"
    else:
        overall = "failed"
    error_message = next(
        (result.get("fail_reason") for result in results if result.get("fail_reason")),
        None,
    )
    if error_message is None and any(
        result.get("capture_type") == "ping"
        and (result.get("parsed") or {}).get("reachable") is False
        for result in results
    ):
        error_message = "目标不可达"
    return overall, reachability, error_message


def _insert_results(
    result_id: str,
    device_id: str,
    results: list[dict],
) -> None:
    for result in results:
        db.insert_collection_item(
            result_id=result_id,
            device_id=device_id,
            source=result.get("source") or "ssh",
            capture_type=result.get("capture_type"),
            command_or_tool=result.get("command"),
            collection_status="success" if result.get("status") == "success" else "failed",
            observation_status=result.get("observation_status"),
            raw_output=result.get("raw_output"),
            parsed=result.get("parsed"),
            started_at=result.get("started_at"),
            finished_at=result.get("finished_at"),
            error_code=result.get("error_code"),
            error_message=result.get("fail_reason"),
        )


def run_collection(
    devices_arg: str | None = None,
    trigger_type: str = "manual",
    requested_by: str = "cli",
    source_ref: str | None = None,
    result_type: str | None = None,
) -> dict:
    """执行一次采集，返回机器可读摘要。"""
    db.init_db()
    # SQLite is the runtime inventory. JSON is used only by bootstrap seeding
    # or by an explicit inventory import.
    devices = []
    for record in db.list_active_devices():
        raw = db._json_value(record.get("raw_json")) or {}
        devices.append(
            {
                **raw,
                "device_id": record["device_id"],
                "device_name": record["device_name"],
                "device_type": record.get("device_type"),
                "mgmt_ip": record.get("mgmt_ip"),
                "site": record.get("site"),
                "role": record.get("role"),
                "vendor": record.get("vendor"),
                "model": record.get("model"),
                "ssh_port": raw.get("ssh_port", 22),
                "snmp_port": raw.get("snmp_port"),
            }
        )
    inventory_version_id = db.get_application_setting("active_inventory_version_id")
    logger.info("已加载设备台账，共 %d 台设备", len(devices))

    username = os.getenv("DEVICE_USERNAME")
    password = os.getenv("DEVICE_PASSWORD")
    community = os.getenv("SNMP_COMMUNITY", "public")
    if not username or not password:
        raise CollectionConfigurationError(
            "未配置 DEVICE_USERNAME / DEVICE_PASSWORD，无法执行设备采集"
        )

    missing = []
    if devices_arg:
        names = [name.strip() for name in devices_arg.split(",") if name.strip()]
        by_name = {device["device_name"]: device for device in devices}
        missing = [name for name in names if name not in by_name]
        if missing:
            logger.warning("未在台账中找到设备: %s", ", ".join(missing))
        selected = [by_name[name] for name in names if name in by_name]
        if not selected:
            raise CollectionConfigurationError("未找到任何指定的设备")
        mode = "selected"
    else:
        selected = devices
        mode = "full"

    hq_server = next((device for device in devices if device["device_name"] == "HQ-SRV"), None)
    hq_router = next((device for device in devices if device["device_name"] == "HQ-R1"), None)
    scope_type = "batch" if len(selected) > 1 else "device"
    capture_result_type = result_type or (
        "single" if trigger_type in ("llm_tool", "mcp") else "full"
    )
    run_id = db.start_collection_run(
        trigger_type=trigger_type,
        scope_type=scope_type,
        requested_by=requested_by,
        source_ref=source_ref,
        inventory_version_id=inventory_version_id,
        scope={"devices": [device["device_name"] for device in selected]},
        tool_version="phase2",
    )
    logger.info("采集任务已创建: %s", run_id)

    ssh_summary = []
    snmp_summary = []
    connectivity_tests = 0
    connectivity_successes = 0
    connectivity_failures = 0
    total_success = 0
    total_failed = 0

    try:
        for device in selected:
            device_name = device["device_name"]
            logger.info("开始采集设备: %s (%s)", device_name, device.get("mgmt_ip"))
            result_id = db.create_device_capture_result(
                run_id,
                device["device_id"],
                result_type=capture_result_type,
                started_at=_utc_now(),
            )
            results = []

            if device.get("device_type") != "linux":
                ssh_results = collect_device(device, username, password)
                ssh_summary.append({"device_name": device_name, "results": ssh_results})
                results.extend(ssh_results)

            if device.get("device_type") != "linux" and device.get("snmp_port"):
                snmp_result = collect_device_snmp(device, community)
                snmp_summary.append({"device_name": device_name, "result": snmp_result})
                results.append(snmp_result)

            if device.get("device_type") == "linux":
                ping_result = None
                if hq_router and device.get("mgmt_ip"):
                    # VPCS terminals have no SSH server; use a router that can
                    # reach the terminal subnet as the ping source.
                    ping_result = collect_router_ping(
                        hq_router,
                        device["mgmt_ip"],
                        username,
                        password,
                    )
                elif (
                    hq_server
                    and device_name != "HQ-SRV"
                    and hq_server.get("mgmt_ip")
                ):
                    ping_result = collect_terminal_ping(
                        device,
                        hq_server["mgmt_ip"],
                        username,
                        password,
                    )
                if ping_result:
                    results.append(ping_result)
                    connectivity_tests += 1
                    if _result_ok(ping_result):
                        connectivity_successes += 1
                    else:
                        connectivity_failures += 1

            _insert_results(result_id, device["device_id"], results)
            overall, reachability, error_message = _device_overall(results)
            db.finalize_device_capture_result(
                result_id,
                reachability=reachability,
                overall_status=overall,
                error_code="device_capture_failed" if overall == "failed" else None,
                error_message=error_message,
                finished_at=_utc_now(),
                publish=True,
            )
            success, failed = _status_counts(results)
            total_success += success
            total_failed += failed

        db.build_topology_snapshot(run_id)
        run_status = "success" if total_failed == 0 else (
            "partial" if total_success else "failed"
        )
        summary = {
            "mode": mode,
            "run_id": run_id,
            "trigger_type": trigger_type,
            "result_type": capture_result_type,
            "device_count": len(selected),
            "requested_devices": None if not devices_arg else [
                name.strip() for name in devices_arg.split(",") if name.strip()
            ],
            "missing_devices": missing,
            "ssh_commands_success": sum(
                1
                for item in ssh_summary
                for result in item["results"]
                if result.get("status") == "success"
            ),
            "ssh_commands_failed": sum(
                1
                for item in ssh_summary
                for result in item["results"]
                if result.get("status") != "success"
            ),
            "snmp_devices_success": sum(
                1 for item in snmp_summary if item["result"].get("status") == "success"
            ),
            "snmp_devices_failed": sum(
                1 for item in snmp_summary if item["result"].get("status") != "success"
            ),
            "connectivity_tests": connectivity_tests,
            "connectivity_successes": connectivity_successes,
            "connectivity_failures": connectivity_failures,
            "inventory_version_id": inventory_version_id,
            "published_at_utc": _utc_now(),
        }
        db.finish_collection_run(run_id, run_status, summary, publish=True)
        logger.info("采集完成: %s", summary)
        return summary
    except Exception:
        db.finish_collection_run(
            run_id,
            "failed",
            {
                "mode": mode,
                "run_id": run_id,
                "error": "collection_exception",
            },
            publish=True,
        )
        raise


def main(argv=None):
    args = parse_args(argv)
    try:
        result = run_collection(
            args.devices,
            trigger_type=args.trigger,
            requested_by=args.requested_by,
            source_ref=args.source_ref,
        )
    except CollectionConfigurationError as exc:
        logger.error("%s", exc)
        return 2
    logger.info("采集摘要: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
