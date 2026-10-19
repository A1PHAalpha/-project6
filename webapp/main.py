"""
FastAPI Web 应用：企业网络智能运维诊断系统主入口。

页面/接口：
- GET  /                      设备状态总览
- GET  /device/{device_name}  单设备详情（采集历史）
- GET  /diagnose               故障诊断页面
- POST /api/diagnose            执行诊断（RAG）
- GET  /api/diagnoses           历史诊断记录
- POST /api/manual/confirm       人工确认处理（备份）
- POST /api/manual/recheck       人工复测
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
from control.manual_change_workflow import confirm_and_backup, recheck_connectivity, reject_action
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


class DiagnoseRequest(BaseModel):
    question: str
    use_kb: bool = True


@app.post("/api/diagnose")
def api_diagnose(req: DiagnoseRequest):
    result = diagnose(req.question, use_kb=req.use_kb)
    return JSONResponse(result)


@app.get("/api/diagnoses")
def api_list_diagnoses(limit: int = 50):
    return db.list_diagnoses(limit=limit)


@app.get("/api/diagnosis/{diagnosis_id}")
def api_get_diagnosis(diagnosis_id: int):
    record = db.get_diagnosis(diagnosis_id)
    if not record:
        return JSONResponse({"error": "未找到该诊断记录"}, status_code=404)
    actions = db.list_manual_actions(diagnosis_id=diagnosis_id)
    return {"diagnosis": record, "manual_actions": actions}


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
