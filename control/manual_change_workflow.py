"""
人工变更流程编排：
诊断建议 -> 人工审核确认 -> 自动备份当前配置 -> 人工在设备上执行变更（本系统不自动下发配置命令）
-> 复测连通性/状态 -> 记录处理结果到 manual_actions 表。

这是方案要求"模型建议不得直接执行配置变更，修改须人工确认、备份后操作并复测"的落地实现。
系统本身不包含任何自动下发配置命令的代码路径。
"""
import logging

from collector import db
from collector.connectivity_test import ping

from .backup_manager import backup_running_config

logger = logging.getLogger("manual_change_workflow")


def confirm_and_backup(diagnosis_id: int, device: dict, operator: str, action_desc: str,
                        username: str, password: str) -> dict:
    """
    第一步：人工点击"确认处理"后调用。自动备份目标设备配置，
    并创建一条 status='confirmed' 的人工操作记录，等待人工去设备上实际执行变更。
    """
    backup_result = backup_running_config(device, username, password)
    status = "confirmed" if backup_result["status"] == "success" else "backup_failed"

    action_id = db.insert_manual_action(
        diagnosis_id=diagnosis_id,
        operator=operator,
        action_desc=action_desc,
        backup_ref=backup_result.get("backup_path"),
        recheck_result=None,
        status=status,
    )

    return {
        "action_id": action_id,
        "status": status,
        "backup_result": backup_result,
    }


def recheck_connectivity(action_id: int, source_device_name: str, target_ip: str, operator: str) -> dict:
    """
    第二步：人工完成设备变更后，点击"复测"调用。执行连通性测试并更新该操作记录的复测结果。
    """
    result = ping(target_ip)
    recheck_text = (
        f"复测目标 {target_ip}: {'可达' if result['success'] else '不可达'} "
        f"(操作人: {operator})"
    )

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE manual_actions SET recheck_result=?, status=? WHERE id=?",
            (recheck_text, "completed" if result["success"] else "recheck_failed", action_id),
        )

    db.insert_collection_record(
        source_device_name, status="success" if result["success"] else "failed",
        command=f"ping {target_ip} (复测)", raw_output=result["raw_output"],
        parsed={"recheck_for_action_id": action_id, "reachable": result["success"]},
        fail_reason=None if result["success"] else f"复测后目标 {target_ip} 仍不可达",
    )

    return {"action_id": action_id, "recheck_text": recheck_text, "success": result["success"]}


def reject_action(diagnosis_id: int, operator: str, reason: str) -> int:
    """人工驳回某条诊断建议，不执行任何变更。"""
    return db.insert_manual_action(
        diagnosis_id=diagnosis_id, operator=operator,
        action_desc=f"驳回建议，原因: {reason}", backup_ref=None,
        recheck_result=None, status="rejected",
    )
