"""Legacy JSON APIs retained for existing pages and integrations."""
import json
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from collector import db
from control.change_workflow import (
    backup_change_config,
    record_manual_execution,
    record_physical,
    run_change_recheck,
)
from control.manual_change_workflow import confirm_and_backup, recheck_connectivity, reject_action
from ops_tools import invoke_tool, list_tool_definitions
from project_config import env_flag
from rag_engine.diagnosis_engine import diagnose
from webapp.report_export import export_diagnosis_report

router = APIRouter(tags=["legacy-api"])


def _audit(
    actor: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    details: dict | None = None,
):
    try:
        db.record_audit_event(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    except Exception:
        # Audit failures must not hide the primary operation result.
        pass


def _require_permission(actor: str, permission: str) -> bool:
    if not env_flag("APP_RBAC_ENFORCE", False):
        return True
    return db.user_has_permission(actor, permission)


class DiagnoseRequest(BaseModel):
    question: str
    use_kb: bool = True
    session_id: str | None = None
    requested_by: str = "web"


class ToolInvokeRequest(BaseModel):
    arguments: dict = {}
    session_id: str | None = None
    message_id: str | None = None
    requested_by: str = "api"


class ChangeCreateRequest(BaseModel):
    diagnosis_id: int | None = None
    conclusion_id: str | None = None
    session_id: str | None = None
    device_name: str
    change_type: str = "software"
    title: str
    description: str | None = None
    risk_level: str = "medium"
    operator: str = "web"
    rollback_plan: str | None = None
    planned_at_utc: str | None = None


class ChangeStepRequest(BaseModel):
    step_type: str
    source_type: str = "human"
    title: str
    command_text: str | None = None
    expected_result: str | None = None
    requires_approval: bool = False
    operator: str = "web"


class ChangeApprovalRequest(BaseModel):
    approver: str
    decision: str = "approved"
    comment: str | None = None


class ChangeOperatorRequest(BaseModel):
    operator: str


class CommandExecutionRequest(BaseModel):
    command_text: str
    output: str | None = None
    stderr: str | None = None
    exit_code: int | None = 0
    result_summary: str | None = None
    operator: str


class PhysicalActionRequest(BaseModel):
    action_type: str
    description: str
    result: str | None = None
    evidence_text: str | None = None
    operator: str


class ChangeRecheckRequest(BaseModel):
    source_device_name: str
    target_ip: str
    expected_reachable: bool = True
    operator: str


class ChangeCompleteRequest(BaseModel):
    operator: str
    outcome: str = "completed"


class ConfirmRequest(BaseModel):
    diagnosis_id: int
    device_name: str
    operator: str
    action_desc: str


class RecheckRequest(BaseModel):
    action_id: int
    source_device_name: str
    target_ip: str
    operator: str


class RejectRequest(BaseModel):
    diagnosis_id: int
    operator: str
    reason: str


@router.post("/api/diagnose")
def api_diagnose(req: DiagnoseRequest):
    result = diagnose(
        req.question,
        use_kb=req.use_kb,
        session_id=req.session_id,
        requested_by=req.requested_by,
    )
    _audit(
        req.requested_by,
        "diagnosis.run",
        "diagnosis",
        str(result.get("diagnosis_id")),
        {
            "session_id": result.get("session_id"),
            "use_kb": req.use_kb,
            "model_id": result.get("model_id"),
        },
    )
    return JSONResponse(result)


@router.get("/api/diagnoses")
def api_list_diagnoses(limit: int = 50):
    return db.list_diagnoses(limit=limit)


@router.get("/api/diagnosis/{diagnosis_id}")
def api_get_diagnosis(diagnosis_id: int):
    detail = db.get_diagnosis_detail(diagnosis_id)
    if not detail:
        return JSONResponse({"error": "未找到该诊断记录"}, status_code=404)
    actions = db.list_manual_actions(diagnosis_id=diagnosis_id)
    return {**detail, "manual_actions": actions}


@router.get("/api/sessions")
def api_list_sessions(limit: int = 50):
    return db.list_agent_sessions(limit=limit)


@router.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    session = db.get_agent_session(session_id)
    if not session:
        return JSONResponse({"error": "未找到该会话"}, status_code=404)
    return session


@router.get("/api/tools")
def api_list_tools():
    return list_tool_definitions()


@router.get("/api/health")
def api_health():
    return {
        "system_status": db.list_system_status(),
        "checks": db.list_health_checks(limit=20),
    }


@router.get("/api/knowledge-base/status")
def api_knowledge_status():
    return db.knowledge_catalog_summary()


@router.get("/api/evaluations")
def api_list_evaluations(limit: int = 50):
    return db.list_experiments(limit=limit)


@router.get("/api/evaluations/{experiment_id}")
def api_get_evaluation(experiment_id: str):
    experiment = db.get_experiment(experiment_id)
    if not experiment:
        return JSONResponse({"error": "未找到该评测实验"}, status_code=404)
    return experiment


@router.get("/api/audit")
def api_list_audit(
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 100,
):
    return db.list_audit_log(target_type=target_type, target_id=target_id, limit=limit)


@router.get("/api/users")
def api_list_users():
    return db.list_users()


@router.post("/api/tools/{tool_name}")
def api_invoke_tool(tool_name: str, req: ToolInvokeRequest):
    try:
        return invoke_tool(
            tool_name,
            req.arguments,
            session_id=req.session_id,
            message_id=req.message_id,
            requested_by=req.requested_by,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


@router.post("/api/changes")
def api_create_change(req: ChangeCreateRequest):
    if not req.diagnosis_id and not req.conclusion_id:
        return JSONResponse(
            {"error": "变更必须关联一次诊断结论（diagnosis_id 或 conclusion_id）"},
            status_code=400,
        )
    if not _require_permission(req.operator, "change.create"):
        return JSONResponse({"error": "当前操作人没有创建变更权限"}, status_code=403)
    try:
        result = db.create_change_request(
            diagnosis_id=req.diagnosis_id,
            conclusion_id=req.conclusion_id,
            session_id=req.session_id,
            device_name=req.device_name,
            change_type=req.change_type,
            title=req.title,
            description=req.description,
            risk_level=req.risk_level,
            operator=req.operator,
            rollback_plan=req.rollback_plan,
            planned_at_utc=req.planned_at_utc,
            status="pending_approval",
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(
        req.operator,
        "change.create",
        "change",
        result["change_id"],
        {"device_name": req.device_name, "change_type": req.change_type},
    )
    return result


@router.get("/api/changes")
def api_list_changes(limit: int = 100, status: str | None = None):
    return db.list_changes(limit=limit, status=status)


@router.get("/api/changes/{change_id}")
def api_get_change(change_id: str):
    detail = db.get_change(change_id)
    if not detail:
        return JSONResponse({"error": "未找到该变更记录"}, status_code=404)
    return detail


@router.post("/api/changes/{change_id}/steps")
def api_add_change_step(change_id: str, req: ChangeStepRequest):
    try:
        return db.add_change_step(
            change_id,
            step_type=req.step_type,
            source_type=req.source_type,
            title=req.title,
            command_text=req.command_text,
            expected_result=req.expected_result,
            requires_approval=req.requires_approval,
            operator=req.operator,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/api/changes/{change_id}/approve")
def api_approve_change(change_id: str, req: ChangeApprovalRequest):
    if not _require_permission(req.approver, "change.approve"):
        return JSONResponse({"error": "当前审批人没有审批权限"}, status_code=403)
    try:
        result = db.approve_change(
            change_id,
            approver=req.approver,
            decision=req.decision,
            comment=req.comment,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(
        req.approver,
        "change.approve",
        "change",
        change_id,
        {"decision": req.decision, "comment": req.comment},
    )
    return result


@router.post("/api/changes/{change_id}/start")
def api_start_change(change_id: str, req: ChangeOperatorRequest):
    if not _require_permission(req.operator, "change.execute"):
        return JSONResponse({"error": "当前操作人没有执行权限"}, status_code=403)
    try:
        result = db.start_change(change_id, operator=req.operator)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(req.operator, "change.start", "change", change_id)
    return result


@router.post("/api/changes/{change_id}/backup")
def api_backup_change(change_id: str, req: ChangeOperatorRequest):
    if not _require_permission(req.operator, "change.execute"):
        return JSONResponse({"error": "当前操作人没有执行权限"}, status_code=403)
    try:
        result = backup_change_config(change_id, req.operator)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(req.operator, "change.backup", "change", change_id, result)
    return JSONResponse(result, status_code=200 if result.get("status") == "success" else 500)


@router.post("/api/changes/{change_id}/steps/{step_id}/execute")
def api_record_command(change_id: str, step_id: str, req: CommandExecutionRequest):
    if not _require_permission(req.operator, "change.execute"):
        return JSONResponse({"error": "当前操作人没有执行权限"}, status_code=403)
    try:
        result = record_manual_execution(
            change_id,
            step_id,
            command_text=req.command_text,
            output=req.output,
            stderr=req.stderr,
            exit_code=req.exit_code,
            operator=req.operator,
            result_summary=req.result_summary,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(req.operator, "change.command_executed", "change", change_id, result)
    return result


@router.post("/api/changes/{change_id}/steps/{step_id}/physical")
def api_record_physical(change_id: str, step_id: str, req: PhysicalActionRequest):
    if not _require_permission(req.operator, "change.execute"):
        return JSONResponse({"error": "当前操作人没有执行权限"}, status_code=403)
    try:
        result = record_physical(
            change_id,
            step_id,
            action_type=req.action_type,
            description=req.description,
            result=req.result,
            evidence_text=req.evidence_text,
            operator=req.operator,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(req.operator, "change.physical_action", "change", change_id, result)
    return result


@router.post("/api/changes/{change_id}/recheck")
def api_change_recheck(change_id: str, req: ChangeRecheckRequest):
    if not _require_permission(req.operator, "change.execute"):
        return JSONResponse({"error": "当前操作人没有执行权限"}, status_code=403)
    try:
        result = run_change_recheck(
            change_id,
            source_device_name=req.source_device_name,
            target_ip=req.target_ip,
            expected_reachable=req.expected_reachable,
            operator=req.operator,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(req.operator, "change.recheck", "change", change_id, result)
    return result


@router.post("/api/changes/{change_id}/complete")
def api_complete_change(change_id: str, req: ChangeCompleteRequest):
    if not _require_permission(req.operator, "change.execute"):
        return JSONResponse({"error": "当前操作人没有执行权限"}, status_code=403)
    try:
        result = db.complete_change(change_id, operator=req.operator, outcome=req.outcome)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _audit(req.operator, "change.complete", "change", change_id, result)
    return result


@router.post("/api/manual/confirm")
def api_manual_confirm(req: ConfirmRequest):
    devices = {device["device_name"]: device for device in db.list_devices()}
    device = devices.get(req.device_name)
    if not device:
        return JSONResponse({"error": f"设备 {req.device_name} 不存在"}, status_code=404)

    device_full = json.loads(device["raw_json"])
    result = confirm_and_backup(
        req.diagnosis_id,
        device_full,
        req.operator,
        req.action_desc,
        os.getenv("DEVICE_USERNAME"),
        os.getenv("DEVICE_PASSWORD"),
    )
    return result


@router.post("/api/manual/recheck")
def api_manual_recheck(req: RecheckRequest):
    return recheck_connectivity(req.action_id, req.source_device_name, req.target_ip, req.operator)


@router.post("/api/manual/reject")
def api_manual_reject(req: RejectRequest):
    action_id = reject_action(req.diagnosis_id, req.operator, req.reason)
    return {"action_id": action_id, "status": "rejected"}


@router.get("/api/export/{diagnosis_id}")
def api_export_report(diagnosis_id: int):
    path = export_diagnosis_report(diagnosis_id)
    if not path:
        return JSONResponse({"error": "未找到该诊断记录"}, status_code=404)
    _audit("web", "report.export", "diagnosis", str(diagnosis_id), {"path": path})
    return FileResponse(path, filename=os.path.basename(path), media_type="text/markdown")
