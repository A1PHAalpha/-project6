"""
LLM 调用封装。使用 OpenAI 兼容接口，支持通义千问/DeepSeek 等平台
（它们均提供 OpenAI SDK 兼容的 base_url）。

安全要求：
- API Key 只从环境变量读取，不写入代码或日志。
- 发送给外部模型的内容已经过脱敏处理（见 sanitize.py），不包含账号密码等凭据。
"""
import json
import logging
import os
import time
from urllib.parse import urlparse

from openai import OpenAI

logger = logging.getLogger("llm_client")

DEFAULT_MAX_TOKENS = 3000
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 60.0

_client = None


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default
    if not minimum <= value <= maximum:
        return default
    return value


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default
    if not minimum <= value <= maximum:
        return default
    return value


def get_temperature() -> float:
    return _read_float("LLM_TEMPERATURE", DEFAULT_TEMPERATURE, 0.0, 2.0)


def get_max_tokens() -> int:
    return _read_int("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS, 128, 8192)


def get_timeout() -> float:
    return _read_float("LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS, 1.0, 600.0)


def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        if not api_key:
            raise RuntimeError("未配置 LLM_API_KEY，请检查 .env 文件")
        _client = OpenAI(api_key=api_key, base_url=base_url, timeout=get_timeout())
    return _client


def get_model_id() -> str:
    return os.getenv("LLM_MODEL", "qwen-plus")


def get_provider_name() -> str:
    base_url = os.getenv("LLM_BASE_URL") or ""
    hostname = urlparse(base_url).hostname
    return hostname or "openai_compatible"


def chat_completion(
    messages: list,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format_json: bool = True,
):
    """
    调用 LLM，返回 (content_text, elapsed_ms, params_used)。
    response_format_json: 若模型支持，要求返回 JSON 格式。
    """
    client = get_client()
    model = get_model_id()
    temperature = get_temperature() if temperature is None else float(temperature)
    max_tokens = get_max_tokens() if max_tokens is None else int(max_tokens)
    params = {"temperature": temperature, "max_tokens": max_tokens}

    kwargs = dict(model=model, messages=messages, **params)
    if response_format_json:
        kwargs["response_format"] = {"type": "json_object"}

    start = time.time()
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        # 部分兼容接口不支持 response_format，降级重试一次
        if response_format_json:
            logger.warning("带 response_format 调用失败(%s)，降级为普通模式重试", e)
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs)
        else:
            raise
    elapsed_ms = int((time.time() - start) * 1000)

    choice = resp.choices[0]
    content = choice.message.content or ""
    finish_reason = getattr(choice, "finish_reason", None)
    usage = getattr(resp, "usage", None)
    usage_data = {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    } if usage else {}
    params_used = {
        "model": model,
        **params,
        "_provider": get_provider_name(),
        "_request_id": getattr(resp, "id", None),
        "_usage": usage_data,
        "_finish_reason": finish_reason,
        "_status": "success",
    }
    return content, elapsed_ms, params_used


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "；".join(_as_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_refs(value) -> list[str]:
    return [str(item) for item in _as_list(value) if item]


_DIAGNOSIS_KEYS = {
    "fault_phenomenon",
    "evidence_used",
    "possible_causes",
    "check_steps",
    "fix_suggestions",
    "recheck_method",
    "missing_information",
}


def _normalize_answer(answer) -> dict | None:
    """Keep model JSON fields in the types expected by the database/UI."""
    if not isinstance(answer, dict):
        return None
    if not any(key in answer for key in _DIAGNOSIS_KEYS):
        return answer

    normalized = dict(answer)
    for key in ("fault_phenomenon", "recheck_method", "missing_information"):
        normalized[key] = _as_text(normalized.get(key))

    normalized["check_steps"] = [
        _as_text(step)
        for step in _as_list(normalized.get("check_steps"))
        if _as_text(step)
    ]

    evidence = []
    for item in _as_list(normalized.get("evidence_used")):
        if isinstance(item, dict):
            entry = dict(item)
            entry["finding"] = _as_text(entry.get("finding"))
            evidence.append(entry)
        elif item:
            evidence.append(
                {
                    "observation_id": None,
                    "device_name": None,
                    "collected_at": None,
                    "finding": _as_text(item),
                }
            )
    normalized["evidence_used"] = evidence

    causes = []
    for item in _as_list(normalized.get("possible_causes")):
        if isinstance(item, dict):
            cause = dict(item)
            cause["cause"] = _as_text(cause.get("cause"))
            cause["kb_refs"] = _as_refs(cause.get("kb_refs"))
            cause["confidence"] = _as_text(cause.get("confidence")) or "medium"
            causes.append(cause)
        elif item:
            causes.append(
                {
                    "cause": _as_text(item),
                    "kb_refs": [],
                    "confidence": "medium",
                }
            )
    normalized["possible_causes"] = causes

    suggestions = []
    for item in _as_list(normalized.get("fix_suggestions")):
        if isinstance(item, dict):
            suggestion = dict(item)
            suggestion["action"] = _as_text(suggestion.get("action"))
            suggestion["command"] = (
                _as_text(suggestion.get("command"))
                if suggestion.get("command") not in (None, "")
                else None
            )
            suggestion["target_device"] = (
                _as_text(suggestion.get("target_device")) or None
            )
            suggestion["kb_refs"] = _as_refs(suggestion.get("kb_refs"))
            suggestion["requires_manual_confirmation"] = bool(
                suggestion.get("requires_manual_confirmation", True)
            )
            suggestions.append(suggestion)
        elif item:
            suggestions.append(
                {
                    "action": _as_text(item),
                    "command": None,
                    "target_device": None,
                    "kb_refs": [],
                    "requires_manual_confirmation": True,
                }
            )
    normalized["fix_suggestions"] = suggestions
    return normalized


def safe_parse_json(text: str) -> dict:
    """尝试解析模型输出为 JSON；若失败则包装为错误结构，不让异常直接中断诊断流程。"""
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            normalized = _normalize_answer(json.loads(candidate))
            if normalized is not None:
                return normalized
        except Exception:
            continue

    logger.error("模型输出无法解析为JSON: %s", text[:300])
    return {
        "fault_phenomenon": "模型输出格式解析失败",
        "evidence_used": [],
        "possible_causes": [],
        "check_steps": [],
        "fix_suggestions": [],
        "recheck_method": "",
        "missing_information": f"模型原始输出无法解析为预期JSON结构，原始内容已记录。原文片段: {text[:300]}",
    }
