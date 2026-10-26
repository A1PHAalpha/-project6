"""Safe read/write support for the whitelisted local environment settings."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values

from project_config import PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"
SECRET_KEYS = {"LLM_API_KEY", "DEVICE_PASSWORD", "SNMP_COMMUNITY", "APP_API_TOKEN"}
SETTING_KEYS = {
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_PROVIDER",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT",
    "LLM_MAX_TOKENS",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "EMBEDDING_CACHE_PATH",
    "FASTEMBED_CACHE_PATH",
    "EMBEDDING_OFFLINE",
    "HF_HUB_OFFLINE",
    "MODEL_SOURCE",
}

DEFAULTS = {
    "LLM_BASE_URL": "",
    "LLM_MODEL": "",
    "LLM_PROVIDER": "OpenAI Compatible",
    "LLM_API_KEY": "",
    "LLM_TEMPERATURE": "0.2",
    "LLM_TIMEOUT": "60",
    "LLM_MAX_TOKENS": "3000",
    "EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5",
    "EMBEDDING_DIMENSION": "512",
    "EMBEDDING_CACHE_PATH": "./models/fastembed",
    "FASTEMBED_CACHE_PATH": "./models/fastembed",
    "EMBEDDING_OFFLINE": "0",
    "HF_HUB_OFFLINE": "0",
    "MODEL_SOURCE": "HuggingFace Hub",
}


def _read_values(path: Path | None = None) -> dict[str, str]:
    target = path or ENV_PATH
    values = {**DEFAULTS, **{key: str(value or "") for key, value in os.environ.items() if key in SETTING_KEYS}}
    if target.exists():
        parsed = dotenv_values(target)
        values.update(
            {key: str(value or "") for key, value in parsed.items() if key in SETTING_KEYS}
        )
    return values


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:3]}{'•' * min(16, len(value) - 6)}{value[-3:]}"


def read_settings(path: Path | None = None) -> dict:
    values = _read_values(path)
    display = {}
    for key in SETTING_KEYS:
        value = values.get(key, "")
        display[key] = mask_secret(value) if key in SECRET_KEYS else value
    return {
        "values": display,
        "masked_keys": sorted(SECRET_KEYS & SETTING_KEYS),
        "env_path": str(path or ENV_PATH),
        "needs_restart": False,
    }


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(char in value for char in (" ", "#", "'", '"')):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _merge_env_text(original: str, updates: dict[str, str]) -> str:
    pending = dict(updates)
    lines = original.splitlines()
    output: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in pending:
            output.append(f"{key}={_quote_env_value(pending.pop(key))}")
        else:
            output.append(line)
    if pending and output and output[-1].strip():
        output.append("")
    for key, value in pending.items():
        output.append(f"{key}={_quote_env_value(value)}")
    return "\n".join(output).rstrip() + "\n"


def save_settings(updates: dict, *, path: Path | None = None) -> dict:
    target = path or ENV_PATH
    invalid = sorted(set(updates) - SETTING_KEYS)
    if invalid:
        raise ValueError(f"不允许修改以下环境变量: {', '.join(invalid)}")
    clean: dict[str, str] = {}
    for key, value in updates.items():
        text = "" if value is None else str(value).strip()
        # A masked or blank secret means "preserve the existing value".
        if key in SECRET_KEYS and ("•" in text or text == ""):
            continue
        clean[key] = text
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    updated = _merge_env_text(original, clean)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {
        "saved": True,
        "updated_keys": sorted(clean),
        "needs_restart": True,
        "settings": read_settings(target),
    }


def test_llm_connection() -> dict:
    from rag_engine.llm_client import chat_completion

    try:
        _content, elapsed_ms, meta = chat_completion(
            [{"role": "user", "content": "仅回复 OK"}]
        )
        return {
            "status": "ok",
            "elapsed_ms": elapsed_ms,
            "model": meta.get("model"),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "message": f"{type(exc).__name__}: {exc}",
        }


def test_embedding() -> dict:
    from knowledge_base.build_index import get_embedding_function
    from knowledge_base.embedding import MODEL_NAME

    try:
        function = get_embedding_function()
        # FastEmbed's callable interface verifies local model availability without
        # mutating the knowledge base.
        vectors = function(["连接测试"])
        return {
            "status": "ok",
            "model": MODEL_NAME,
            "dimension": len(vectors[0]) if vectors else None,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
