"""Post-processing for model answers before they are persisted or shown."""

from __future__ import annotations

import re

from collector import db

_COMMAND_SEPARATORS = ("\n", "\r", ";", "&&", "||")
_IP_PATTERN = r"(?:\d{1,3}\.){3}\d{1,3}"


def _disable_multisegment_commands(answer: dict) -> None:
    suggestions = []
    for item in answer.get("fix_suggestions") or []:
        if not isinstance(item, dict):
            suggestions.append(item)
            continue
        suggestion = dict(item)
        command = suggestion.get("command")
        if isinstance(command, str) and any(
            separator in command for separator in _COMMAND_SEPARATORS
        ):
            suggestion["command"] = None
            suggestion["requires_manual_confirmation"] = True
            action = str(suggestion.get("action") or "").strip()
            if "需人工拆分" not in action:
                suggestion["action"] = (
                    f"{action}（建议包含多段命令，需人工拆分后执行）"
                )
        suggestions.append(suggestion)
    answer["fix_suggestions"] = suggestions


def _normalize_recheck_target(answer: dict) -> None:
    text = str(answer.get("recheck_method") or "")
    if "总部服务器" not in text:
        return
    try:
        server = next(
            (
                device
                for device in db.list_active_devices()
                if "服务器" in str(device.get("role") or "")
            ),
            None,
        )
    except Exception:
        server = None
    if not server or not server.get("mgmt_ip"):
        return
    replacement = str(server["mgmt_ip"])
    # Example: "ping 192.168.249.12（总部服务器IP）"
    normalized = re.sub(
        rf"\b{_IP_PATTERN}(?=\s*[（(]?总部服务器)",
        replacement,
        text,
    )
    if normalized != text:
        answer["recheck_method"] = normalized


def validate_answer(answer: dict) -> dict:
    """Apply deterministic safety/consistency checks before persistence."""
    if not isinstance(answer, dict):
        return answer
    validated = dict(answer)
    _disable_multisegment_commands(validated)
    _normalize_recheck_target(validated)
    return validated
