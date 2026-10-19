"""Project-local configuration loading.

All entry points should load the repository's own ``.env`` explicitly.
This avoids ``python-dotenv`` walking up the directory tree and silently
loading credentials from an unrelated project.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env(override: bool = False, dotenv_path: str | Path | None = None) -> Path:
    """Load the project ``.env`` without searching parent directories."""
    target = Path(dotenv_path) if dotenv_path is not None else ENV_FILE
    load_dotenv(dotenv_path=target, override=override)
    return target


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment flag."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
