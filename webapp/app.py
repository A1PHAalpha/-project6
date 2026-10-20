"""FastAPI application assembly."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from project_config import env_flag, load_project_env

load_project_env()

from app_bootstrap import bootstrap_runtime
from control.terminal_service import terminal_manager
from webapp.api.v1 import register_problem_handler
from webapp.api.v1 import router as api_v1_router
from webapp.routers import console, legacy_api, legacy_pages
from webapp.security import TokenAuthMiddleware, get_api_token
from webapp.security import router as auth_router
from webapp.terminal_api import router as terminal_router
from webapp.ui import FRONTEND_ASSETS, STATIC_DIR


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap_runtime()
    try:
        yield
    finally:
        terminal_manager.shutdown()


def create_app() -> FastAPI:
    """Build the application from independently owned route modules."""
    get_api_token()
    enable_docs = env_flag("APP_ENABLE_DOCS")
    docs_kwargs = {} if enable_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(
        title="企业网络智能运维诊断系统",
        lifespan=lifespan,
        **docs_kwargs,
    )
    app.add_middleware(TokenAuthMiddleware)

    app.include_router(auth_router)
    app.include_router(api_v1_router)
    app.include_router(terminal_router)
    app.include_router(legacy_pages.router)
    app.include_router(legacy_api.router)
    register_problem_handler(app)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    if FRONTEND_ASSETS.is_dir():
        app.mount(
            "/console/assets",
            StaticFiles(directory=str(FRONTEND_ASSETS)),
            name="console-assets",
        )
    app.include_router(console.router)
    return app


app = create_app()
