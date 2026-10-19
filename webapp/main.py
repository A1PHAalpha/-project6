"""
FastAPI Web 应用：企业网络智能运维诊断系统主入口。

页面/接口：
- GET  /                      设备状态总览
- GET  /device/{device_name}  单设备详情（采集历史）
- GET  /diagnose               故障诊断页面
- GET  /changes                人工变更列表
- GET  /changes/{id}           人工变更详情
- POST /api/diagnose            执行诊断（RAG）
- GET  /api/diagnoses           历史诊断记录
- GET  /api/sessions            诊断会话列表
- POST /api/tools/{name}        只读工具调用
- POST /api/changes             创建人工变更
- POST /api/changes/{id}/...    审批、步骤、备份、复测
- POST /api/manual/confirm       旧版人工处理兼容入口
- GET  /api/export/{diagnosis_id} 导出单次诊断报告
"""
import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from project_config import env_flag, load_project_env

load_project_env()

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app_bootstrap import bootstrap_runtime
from collector import db
from control.change_workflow import (
    backup_change_config,
    record_manual_execution,
    record_physical,
    run_change_recheck,
)
from control.manual_change_workflow import confirm_and_backup, recheck_connectivity, reject_action
from ops_tools import invoke_tool, list_tool_definitions
from rag_engine.diagnosis_engine import diagnose
from webapp.report_export import export_diagnosis_report
from webapp.security import TokenAuthMiddleware, get_api_token
from webapp.security import router as auth_router

# 启动即校验 APP_API_TOKEN：未配置/过短则直接报错退出，避免"忘配 = 裸奔"
get_api_token()

ENABLE_DOCS = env_flag("APP_ENABLE_DOCS")
_docs_kwargs = {} if ENABLE_DOCS else {"docs_url": None, "redoc_url": None, "openapi_url": None}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap_runtime()
    yield


app = FastAPI(title="企业网络智能运维诊断系统", lifespan=lifespan, **_docs_kwargs)
app.add_middleware(TokenAuthMiddleware)
app.include_router(auth_router)

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    devices = db.list_devices()
    latest_status = db.get_all_latest_status()
    status_by_device = {}
    for rec in latest_status:
        status_by_device.setdefault(rec["device_name"], []).append(rec)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "devices": devices, "status_by_device": status_by_device},
    )


@app.get("/device/{device_name}", response_class=HTMLResponse)
def device_detail(request: Request, device_name: str):
    records = db.get_device_collection_history(device_name, limit=50)
    return templates.TemplateResponse(
        "device_detail.html",
        {"request": request, "device_name": device_name, "records": records},
    )


@app.get("/diagnose", response_class=HTMLResponse)
def diagnose_page(request: Request):
    history = db.list_diagnoses(limit=20)
    return templates.TemplateResponse("diagnose.html", {"request": request, "history": history})


@app.get("/changes", response_class=HTMLResponse)
def changes_page(request: Request):
    changes = db.list_changes(limit=100)
    return templates.TemplateResponse(
        "changes.html",
        {"request": request, "changes": changes},
    )


@app.get("/changes/{change_id}", response_class=HTMLResponse)
def change_detail(request: Request, change_id: str):
    detail = db.get_change(change_id)
    if not detail:
        return templates.TemplateResponse(
            "change_detail.html",
            {"request": request, "detail": None, "error": "未找到该变更记录"},
            status_code=404,
        )
    return templates.TemplateResponse(
        "change_detail.html",
        {"request": request, "detail": detail, "error": None},
    )


class DiagnoseRequest(BaseModel):
    question: str
    use_kb: bool = True
    session_id: str | None = None
    requested_by: str = "web"


@app.post("/api/diagnose")
def api_diagnose(req: DiagnoseRequest):
    result = diagnose(
        req.question,
        use_kb=req.use_kb,
        session_id=req.session_id,
        requested_by=req.requested_by,
    )
    return JSONResponse(result)


@app.get("/api/diagnoses")
def api_list_diagnoses(limit: int = 50):
    return db.list_diagnoses(limit=limit)


@app.get("/api/diagnosis/{diagnosis_id}")
def api_get_diagnosis(diagnosis_id: int):
    detail = db.get_diagnosis_detail(diagnosis_id)
    if not detail:
        return JSONResponse({"error": "未找到该诊断记录"}, status_code=404)
    actions = db.list_manual_actions(diagnosis_id=diagnosis_id)
    return {**detail, "manual_actions": actions}


@app.get("/api/sessions")
def api_list_sessions(limit: int = 50):
    return db.list_agent_sessions(limit=limit)


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    session = db.get_agent_session(session_id)
    if not session:
        return JSONResponse({"error": "未找到该会话"}, status_code=404)
    return session


@app.get("/api/tools")
def api_list_tools():
    return list_tool_definitions()


@app.get("/api/health")
def api_health():
    return {
        "system_status": db.list_system_status(),
        "checks": db.list_health_checks(limit=20),
    }


class ToolInvokeRequest(BaseModel):
    arguments: dict = {}
    session_id: str | None = None
    message_id: str | None = None
    requested_by: str = "api"


@app.post("/api/tools/{tool_name}")
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
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}"},
            status_code=500,
        )


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


@app.post("/api/changes")
def api_create_change(req: ChangeCreateRequest):
    if not req.diagnosis_id and not req.conclusion_id:
        return JSONResponse(
            {"error": "变更必须关联一次诊断结论（diagnosis_id 或 conclusion_id）"},
            status_code=400,
        )
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
    return result


@app.get("/api/changes")
def api_list_changes(limit: int = 100, status: str | None = None):
    return db.list_changes(limit=limit, status=status)


@app.get("/api/changes/{change_id}")
def api_get_change(change_id: str):
    detail = db.get_change(change_id)
    if not detail:
        return JSONResponse({"error": "未找到该变更记录"}, status_code=404)
    return detail


class ChangeStepRequest(BaseModel):
    step_type: str
    source_type: str = "human"
    title: str
    command_text: str | None = None
    expected_result: str | None = None
    requires_approval: bool = False
    operator: str = "web"


@app.post("/api/changes/{change_id}/steps")
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


class ChangeApprovalRequest(BaseModel):
    approver: str
    decision: str = "approved"
    comment: str | None = None


@app.post("/api/changes/{change_id}/approve")
def api_approve_change(change_id: str, req: ChangeApprovalRequest):
    try:
        return db.approve_change(
            change_id,
            approver=req.approver,
            decision=req.decision,
            comment=req.comment,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


class ChangeOperatorRequest(BaseModel):
    operator: str


@app.post("/api/changes/{change_id}/start")
def api_start_change(change_id: str, req: ChangeOperatorRequest):
    try:
        return db.start_change(change_id, operator=req.operator)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/changes/{change_id}/backup")
def api_backup_change(change_id: str, req: ChangeOperatorRequest):
    try:
        result = backup_change_config(change_id, req.operator)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    status_code = 200 if result.get("status") == "success" else 500
    return JSONResponse(result, status_code=status_code)


class CommandExecutionRequest(BaseModel):
    command_text: str
    output: str | None = None
    stderr: str | None = None
    exit_code: int | None = 0
    result_summary: str | None = None
    operator: str


@app.post("/api/changes/{change_id}/steps/{step_id}/execute")
def api_record_command(change_id: str, step_id: str, req: CommandExecutionRequest):
    try:
        return record_manual_execution(
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


class PhysicalActionRequest(BaseModel):
    action_type: str
    description: str
    result: str | None = None
    evidence_text: str | None = None
    operator: str


@app.post("/api/changes/{change_id}/steps/{step_id}/physical")
def api_record_physical(change_id: str, step_id: str, req: PhysicalActionRequest):
    try:
        return record_physical(
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


class ChangeRecheckRequest(BaseModel):
    source_device_name: str
    target_ip: str
    expected_reachable: bool = True
    operator: str


@app.post("/api/changes/{change_id}/recheck")
def api_change_recheck(change_id: str, req: ChangeRecheckRequest):
    try:
        return run_change_recheck(
            change_id,
            source_device_name=req.source_device_name,
            target_ip=req.target_ip,
            expected_reachable=req.expected_reachable,
            operator=req.operator,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


class ChangeCompleteRequest(BaseModel):
    operator: str
    outcome: str = "completed"


@app.post("/api/changes/{change_id}/complete")
def api_complete_change(change_id: str, req: ChangeCompleteRequest):
    try:
        return db.complete_change(change_id, operator=req.operator, outcome=req.outcome)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


class ConfirmRequest(BaseModel):
    diagnosis_id: int
    device_name: str
    operator: str
    action_desc: str


@app.post("/api/manual/confirm")
def api_manual_confirm(req: ConfirmRequest):
    devices = {d["device_name"]: d for d in db.list_devices()}
    device = devices.get(req.device_name)
    if not device:
        return JSONResponse({"error": f"设备 {req.device_name} 不存在"}, status_code=404)

    import json
    device_full = json.loads(device["raw_json"])
    username = os.getenv("DEVICE_USERNAME")
    password = os.getenv("DEVICE_PASSWORD")
    result = confirm_and_backup(req.diagnosis_id, device_full, req.operator, req.action_desc, username, password)
    return result


class RecheckRequest(BaseModel):
    action_id: int
    source_device_name: str
    target_ip: str
    operator: str


@app.post("/api/manual/recheck")
def api_manual_recheck(req: RecheckRequest):
    result = recheck_connectivity(req.action_id, req.source_device_name, req.target_ip, req.operator)
    return result


class RejectRequest(BaseModel):
    diagnosis_id: int
    operator: str
    reason: str


@app.post("/api/manual/reject")
def api_manual_reject(req: RejectRequest):
    action_id = reject_action(req.diagnosis_id, req.operator, req.reason)
    return {"action_id": action_id, "status": "rejected"}


@app.get("/api/export/{diagnosis_id}")
def api_export_report(diagnosis_id: int):
    path = export_diagnosis_report(diagnosis_id)
    if not path:
        return JSONResponse({"error": "未找到该诊断记录"}, status_code=404)
    return FileResponse(path, filename=os.path.basename(path), media_type="text/markdown")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    reload = env_flag("APP_RELOAD")
    uvicorn.run("webapp.main:app", host=host, port=port, reload=reload)
