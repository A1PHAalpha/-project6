"""React console entry point and frontend fallback."""
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from webapp.ui import FRONTEND_INDEX, templates

router = APIRouter(tags=["console"])


@router.get("/console", response_class=HTMLResponse)
@router.get("/console/{spa_path:path}", response_class=HTMLResponse)
def console_spa(spa_path: str = ""):
    if not FRONTEND_INDEX.is_file():
        return HTMLResponse(
            "<h1>Console 尚未构建</h1><p>请在项目根目录执行 python start.py。</p>",
            status_code=503,
        )
    return FileResponse(FRONTEND_INDEX, media_type="text/html")


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    if FRONTEND_INDEX.is_file():
        return RedirectResponse(url="/console/overview", status_code=302)
    return legacy_index(request)


@router.get("/legacy", response_class=HTMLResponse)
def legacy_index(request: Request):
    from collector import db

    devices = db.list_devices()
    latest_status = db.get_all_latest_status()
    status_by_device: dict[str, list[dict]] = {}
    for record in latest_status:
        status_by_device.setdefault(record["device_name"], []).append(record)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "devices": devices, "status_by_device": status_by_device},
    )
