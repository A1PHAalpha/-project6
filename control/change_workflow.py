"""人工变更编排：审批、备份、软件命令、物理操作和真实复测。

本模块不自动下发配置变更。软件命令由人工执行后录入实际命令与输出；
需要执行的只读检查通过一期采集模型落盘。所有动作都写入变更事件流。
"""
from __future__ import annotations

import json
import logging
import os

from collector import db
from collector.connectivity_test import collect_terminal_ping, ping

from .backup_manager import backup_running_config

logger = logging.getLogger("change_workflow")


def _device_from_change(change_detail: dict) -> dict:
    change = change_detail["change"]
    devices = {device["device_name"]: device for device in db.list_devices()}
    device = devices.get(change["device_name"])
    if not device:
        raise ValueError(f"变更关联设备不存在: {change['device_name']}")
    raw = {}
    try:
        raw = json.loads(device.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    merged = {**device, **raw}
    merged["device_name"] = device["device_name"]
    return merged


def backup_change_config(change_id: str, operator: str) -> dict:
    """在人工审批后、执行变更前保存真实运行配置。"""
    detail = db.get_change(change_id)
    if not detail:
        raise ValueError(f"变更不存在: {change_id}")
    change = detail["change"]
    if change["status"] not in ("approved", "executing"):
        raise ValueError("只有已审批或执行中的变更可以备份配置")
    username = os.getenv("DEVICE_USERNAME")
    password = os.getenv("DEVICE_PASSWORD")
    if not username or not password:
        raise ValueError("未配置 DEVICE_USERNAME / DEVICE_PASSWORD")
    device = _device_from_change(detail)
    result = backup_running_config(device, username, password)
    if result.get("status") != "success":
        db.record_change_event(
            change_id,
            "config_backup_failed",
            operator,
            payload={"reason": result.get("reason") or "unknown"},
        )
        return result
    persisted = db.record_config_backup(
        change_id,
        content=result["raw_config"],
        storage_ref=result.get("backup_path"),
        sha256=result.get("sha256"),
        operator=operator,
        backup_type="before",
        verified=True,
    )
    return {
        "status": "success",
        "backup_path": result.get("backup_path"),
        "sha256": result.get("sha256"),
        "backup_id": persisted["backup_id"],
    }


def record_manual_execution(
    change_id: str,
    step_id: str,
    *,
    command_text: str,
    output: str | None,
    exit_code: int | None,
    operator: str,
    stderr: str | None = None,
    result_summary: str | None = None,
) -> dict:
    """记录人工实际执行的命令，不通过 LLM 或 Web 自动下发。"""
    return db.record_command_execution(
        change_id,
        step_id,
        command_text=command_text,
        output=output,
        stderr=stderr,
        exit_code=exit_code,
        operator=operator,
        execution_mode="manual",
        result_summary=result_summary,
    )


def record_physical(
    change_id: str,
    step_id: str,
    *,
    action_type: str,
    description: str,
    operator: str,
    result: str | None = None,
    evidence_text: str | None = None,
) -> dict:
    return db.record_physical_action(
        change_id,
        step_id,
        action_type=action_type,
        description=description,
        operator=operator,
        result=result,
        evidence_text=evidence_text,
    )


def run_change_recheck(
    change_id: str,
    *,
    source_device_name: str,
    target_ip: str,
    operator: str,
    expected_reachable: bool = True,
) -> dict:
    """执行一次真实复测并绑定到变更记录。

    分支终端优先通过 SSH 从源终端执行 ping；如果源设备不是 Linux 或凭据缺失，
    才退回运维主机 ping，并在结果中明确记录 execution_location。
    """
    detail = db.get_change(change_id)
    if not detail:
        raise ValueError(f"变更不存在: {change_id}")
    change = detail["change"]
    if change["status"] not in ("approved", "executing"):
        raise ValueError("只有已审批或执行中的变更可以复测")

    devices = {device["device_name"]: device for device in db.list_devices()}
    source_device = devices.get(source_device_name)
    if not source_device:
        raise ValueError(f"复测源设备不存在: {source_device_name}")
    username = os.getenv("DEVICE_USERNAME")
    password = os.getenv("DEVICE_PASSWORD")
    source_record = {
        "device_name": source_device_name,
        "source": "ping",
        "capture_type": "ping",
        "command": f"ping {target_ip}",
    }
    actual = {
        "target_ip": target_ip,
        "expected_reachable": expected_reachable,
        "source_device": source_device_name,
    }

    terminal_result = None
    if source_device.get("device_type") == "linux" and username and password:
        terminal_result = collect_terminal_ping(
            source_device,
            target_ip,
            username,
            password,
        )
    if terminal_result is not None:
        parsed = terminal_result.get("parsed") or {}
        actual.update(parsed)
        actual["execution_location"] = "source_device"
        source_record.update(
            {
                "status": terminal_result.get("status"),
                "raw_output": terminal_result.get("raw_output"),
                "parsed": parsed,
                "started_at": terminal_result.get("started_at"),
                "finished_at": terminal_result.get("finished_at"),
                "fail_reason": terminal_result.get("fail_reason"),
                "observation_status": terminal_result.get("observation_status"),
            }
        )
        collected = terminal_result.get("status") == "success"
    else:
        host_result = ping(target_ip)
        actual.update(
            {
                "reachable": bool(host_result.get("success")),
                "execution_location": "collector_host",
                "loss_rate": host_result.get("loss_rate"),
            }
        )
        source_record.update(
            {
                "status": "success",
                "raw_output": host_result.get("raw_output"),
                "parsed": {
                    "target_ip": target_ip,
                    "reachable": bool(host_result.get("success")),
                    "source_device": source_device_name,
                    "execution_location": "collector_host",
                },
                "observation_status": "observed",
            }
        )
        collected = True

    batch = db.insert_collection_batch(
        [source_record],
        trigger_type="recheck",
        result_type="recheck",
        requested_by=operator,
        scope_type="recheck",
    )
    observation_id = None
    if batch.get("item_ids"):
        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT observation_id FROM state_observations
                   WHERE item_id=? AND state_type='reachability'
                   ORDER BY created_at_utc DESC LIMIT 1""",
                (batch["item_ids"][0],),
            ).fetchone()
            observation_id = row[0] if row else None

    if not collected or actual.get("reachable") is None:
        pass_fail = "inconclusive"
    elif bool(actual.get("reachable")) == bool(expected_reachable):
        pass_fail = "pass"
    else:
        pass_fail = "fail"
    expected = {"reachable": expected_reachable}
    persisted = db.record_recheck(
        change_id,
        collection_run_id=batch.get("run_id"),
        source_device_id=source_device.get("device_id"),
        source_device_name=source_device_name,
        target=target_ip,
        check_type="ping",
        expected=expected,
        actual=actual,
        pass_fail=pass_fail,
        evidence_observation_id=observation_id,
        operator=operator,
    )
    return {
        **persisted,
        "collection_run_id": batch.get("run_id"),
        "source_device_name": source_device_name,
        "target": target_ip,
        "actual": actual,
    }
