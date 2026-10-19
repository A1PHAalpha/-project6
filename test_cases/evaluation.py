"""结构化评测规则和可复核的评分函数。

评分只依赖本次模型回答和预先声明的期望要点，不把答案写回知识库，
也不会把测试问题或标准答案作为检索资料。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

EXPECTED_BY_ID: dict[str, dict[str, Any]] = {
    "LINK_DOWN_01": {
        "must_mention": ["链路", "down", "接口"],
        "expected_devices": ["BR-A-R2", "BR-A-SW2"],
        "forbidden": ["VLAN 20 是唯一原因"],
        "require_recheck": True,
    },
    "LINK_DOWN_02": {
        "must_mention": ["Gi0/2", "down", "分支B"],
        "expected_devices": ["HQ-SW1"],
        "forbidden": ["确认端口一定连接分支B"],
        "require_recheck": True,
    },
    "VLAN_MISCONFIG_01": {
        "must_mention": ["VLAN", "20", "Gi0/0"],
        "expected_devices": ["BR-A-SW2"],
        "forbidden": ["默认路由缺失是根因"],
        "require_recheck": True,
    },
    "VLAN_MISCONFIG_02": {
        "must_mention": ["trunk", "allowed vlan", "VLAN 10"],
        "expected_devices": ["HQ-SW1"],
        "forbidden": ["ACL 是唯一原因"],
        "require_recheck": True,
    },
    "DEFAULT_ROUTE_MISSING_01": {
        "must_mention": ["默认路由", "0.0.0.0/0", "BR-B-R3"],
        "expected_devices": ["BR-B-R3"],
        "forbidden": ["VLAN 配置错误是根因"],
        "require_recheck": True,
    },
    "DEFAULT_ROUTE_MISSING_02": {
        "must_mention": ["默认路由", "0.0.0.0/0", "BR-A-R2"],
        "expected_devices": ["BR-A-R2"],
        "forbidden": ["OSPF 邻居故障是唯一根因"],
        "require_recheck": True,
    },
    "ACL_OVERBLOCK_01": {
        "must_mention": ["ACL", "deny", "192.168.20.0"],
        "expected_devices": ["HQ-R1"],
        "forbidden": ["默认路由缺失是根因"],
        "require_recheck": True,
    },
    "ACL_OVERBLOCK_02": {
        "must_mention": ["ACL", "ICMP", "deny"],
        "expected_devices": ["HQ-R1"],
        "forbidden": ["VLAN 10 未放通是唯一原因"],
        "require_recheck": True,
    },
    "NORMAL_01": {
        "must_mention": ["正常", "无故障"],
        "expected_devices": ["HQ-R1"],
        "forbidden": ["链路中断", "默认路由缺失", "ACL 误拦截", "shutdown"],
        "require_recheck": False,
        "expect_no_change": True,
    },
    "INSUFFICIENT_EVIDENCE_01": {
        "must_mention": ["缺少", "证据", "BR-B-SW3"],
        "expected_devices": ["BR-B-SW3"],
        "forbidden": ["端口已经关闭", "Gi0/1 已关闭", "shutdown 已执行"],
        "require_recheck": True,
        "insufficient": True,
    },
}


def expected_for_scenario(scenario: dict) -> dict:
    """返回场景的机器可读期望；未登记的场景使用保守默认值。"""
    expected = {
        "must_mention": [],
        "expected_devices": [],
        "forbidden": [],
        "require_recheck": True,
        "expect_no_change": False,
        "insufficient": False,
    }
    expected.update(EXPECTED_BY_ID.get(scenario["id"], {}))
    expected["category"] = scenario["category"]
    expected["expected_notes"] = scenario.get("expected_notes", "")
    return expected


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def answer_text(answer: dict) -> str:
    """聚合回答中真正可评估的文字，避免把问题本身算作命中。"""
    parts = [
        _as_text(answer.get("fault_phenomenon")),
        _as_text(answer.get("missing_information")),
        _as_text(answer.get("recheck_method")),
    ]
    for item in answer.get("possible_causes") or []:
        parts.append(_as_text(item.get("cause") if isinstance(item, dict) else item))
        if isinstance(item, dict):
            parts.append(_as_text(item.get("kb_refs")))
    parts.extend(_as_text(step) for step in (answer.get("check_steps") or []))
    for item in answer.get("fix_suggestions") or []:
        if isinstance(item, dict):
            parts.append(_as_text(item.get("action")))
            parts.append(_as_text(item.get("kb_refs")))
        else:
            parts.append(_as_text(item))
    parts.extend(_as_text(item) for item in (answer.get("evidence_used") or []))
    return "\n".join(part for part in parts if part)


def score_answer(answer: dict, expected: dict) -> dict:
    """按关键词覆盖、设备覆盖和禁止性断言给出可复核评分。"""
    text = answer_text(answer or {})
    lowered = text.lower()
    must_terms = [str(term) for term in expected.get("must_mention") or []]
    expected_devices = [str(term) for term in expected.get("expected_devices") or []]
    forbidden = [str(term) for term in expected.get("forbidden") or []]
    matched = [term for term in must_terms if term.lower() in lowered]
    missing = [term for term in must_terms if term.lower() not in lowered]
    device_matches = [term for term in expected_devices if term.lower() in lowered]
    forbidden_hits = [term for term in forbidden if term.lower() in lowered]
    mention_ratio = len(matched) / len(must_terms) if must_terms else 1.0
    device_ratio = (
        len(device_matches) / len(expected_devices) if expected_devices else 1.0
    )
    coverage = 0.7 * mention_ratio + 0.3 * device_ratio
    if any(marker in lowered for marker in ("大模型调用失败", "模型输出格式解析失败")):
        correctness = "error"
        coverage = 0.0
    elif forbidden_hits:
        correctness = "wrong"
    elif coverage >= 0.75:
        correctness = "correct"
    elif coverage >= 0.4:
        correctness = "partial"
    else:
        correctness = "wrong"

    suggestions = [
        item for item in (answer.get("fix_suggestions") or []) if isinstance(item, dict)
    ]
    recheck = _as_text(answer.get("recheck_method")).strip()
    if expected.get("expect_no_change"):
        risky_actions = [
            item
            for item in suggestions
            if any(
                token in _as_text(item.get("action")).lower()
                for token in ("shutdown", "no shutdown", "ip route", "access-list", "switchport")
            )
        ]
        executability = 1.0
        if risky_actions:
            executability -= 0.5
        if not suggestions:
            executability = 1.0
    else:
        executability = 0.0 if not suggestions else 0.5
        if suggestions:
            if all(
                item.get("requires_manual_confirmation") is True
                for item in suggestions
                if any(
                    token in _as_text(item.get("action")).lower()
                    for token in ("shutdown", "no shutdown", "ip route", "access-list", "switchport")
                )
            ):
                executability += 0.25
            else:
                executability -= 0.25
        if recheck:
            executability += 0.25
        elif expected.get("require_recheck"):
            executability -= 0.25
    executability = max(0.0, min(1.0, executability))

    return {
        "correctness": correctness,
        "correctness_score": round(coverage, 4),
        "executability_score": round(executability, 4),
        "matched_terms": matched,
        "missing_terms": missing,
        "matched_devices": device_matches,
        "forbidden_hits": forbidden_hits,
        "has_recheck": bool(recheck),
        "suggestion_count": len(suggestions),
        "insufficient_evidence": bool(expected.get("insufficient")),
    }


def evidence_hash(evidence: list[dict]) -> str:
    payload = json.dumps(evidence or [], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def aggregate_results(results: list[dict]) -> dict:
    """汇总有/无知识库两组的准确性、可执行性和响应时间。"""
    summary: dict[str, Any] = {
        "total_results": len(results),
        "by_variant": {},
        "comparison": {},
    }
    for enabled, key in ((True, "with_kb"), (False, "without_kb")):
        rows = [row for row in results if bool(row.get("kb_enabled")) == enabled]
        total = len(rows)
        counts = {
            label: sum(1 for row in rows if row.get("correctness") == label)
            for label in ("correct", "partial", "wrong", "error", "skipped")
        }
        accuracy = (
            (counts["correct"] + 0.5 * counts["partial"]) / total if total else 0.0
        )
        times = [
            row.get("total_response_time_ms")
            for row in rows
            if row.get("total_response_time_ms") is not None
        ]
        executability = [
            row.get("executability_score")
            for row in rows
            if row.get("executability_score") is not None
        ]
        summary["by_variant"][key] = {
            "total": total,
            **counts,
            "accuracy": round(accuracy, 4),
            "avg_executability": round(
                sum(executability) / len(executability), 4
            ) if executability else 0.0,
            "avg_total_response_time_ms": round(
                sum(times) / len(times), 1
            ) if times else None,
        }
    with_kb = summary["by_variant"]["with_kb"]
    without_kb = summary["by_variant"]["without_kb"]
    summary["comparison"] = {
        "accuracy_delta": round(
            with_kb["accuracy"] - without_kb["accuracy"], 4
        ),
        "executability_delta": round(
            with_kb["avg_executability"] - without_kb["avg_executability"], 4
        ),
        "response_time_delta_ms": (
            round(
                with_kb["avg_total_response_time_ms"]
                - without_kb["avg_total_response_time_ms"],
                1,
            )
            if with_kb["avg_total_response_time_ms"] is not None
            and without_kb["avg_total_response_time_ms"] is not None
            else None
        ),
    }
    return summary
