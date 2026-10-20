"""Server-rendered pages retained for compatibility."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from collector import db
from webapp.ui import templates

router = APIRouter(tags=["legacy-pages"])


@router.get("/device/{device_name}", response_class=HTMLResponse)
def device_detail(request: Request, device_name: str):
    records = db.get_device_collection_history(device_name, limit=50)
    return templates.TemplateResponse(
        "device_detail.html",
        {"request": request, "device_name": device_name, "records": records},
    )


@router.get("/diagnose", response_class=HTMLResponse)
def diagnose_page(request: Request):
    history = db.list_diagnoses(limit=20)
    return templates.TemplateResponse("diagnose.html", {"request": request, "history": history})


@router.get("/changes", response_class=HTMLResponse)
def changes_page(request: Request):
    changes = db.list_changes(limit=100)
    return templates.TemplateResponse(
        "changes.html",
        {"request": request, "changes": changes},
    )


@router.get("/changes/{change_id}", response_class=HTMLResponse)
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


@router.get("/evaluations", response_class=HTMLResponse)
def evaluations_page(request: Request):
    experiments = db.list_experiments(limit=50)
    return templates.TemplateResponse(
        "evaluations.html",
        {"request": request, "experiments": experiments},
    )


@router.get("/evaluations/{experiment_id}", response_class=HTMLResponse)
def evaluation_detail(request: Request, experiment_id: str):
    experiment = db.get_experiment(experiment_id)
    if not experiment:
        return templates.TemplateResponse(
            "evaluation_detail.html",
            {"request": request, "experiment": None, "error": "未找到该评测实验"},
            status_code=404,
        )
    return templates.TemplateResponse(
        "evaluation_detail.html",
        {"request": request, "experiment": experiment, "error": None},
    )


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(request: Request):
    return templates.TemplateResponse(
        "knowledge.html",
        {"request": request, "knowledge": db.knowledge_catalog_summary()},
    )
