"""Compatibility entry point for ``uvicorn webapp.main:app``."""
import os

from project_config import env_flag, load_project_env

load_project_env()

from webapp.app import app
from webapp.routers.legacy_api import *  # noqa: F403

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "webapp.main:app",
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=env_flag("APP_RELOAD"),
    )
