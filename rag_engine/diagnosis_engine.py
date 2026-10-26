"""
RAG 故障诊断引擎主流程：
问题输入 -> 识别相关设备 -> 取最新采集证据 -> 向量检索知识库 -> 脱敏 ->
组装Prompt -> 调用LLM -> 解析结构化结果 -> 落盘诊断记录（含模型标识/模板/参数/检索来源）。

支持 use_kb=False 模式用于效果测试环节的"无知识库检索"对比。
"""
import logging
import time

from collector import db
from knowledge_base.retriever import retrieve

from .device_extractor import extract_relevant_devices
from .evidence_builder import (
    build_evidence_for_devices,
    format_evidence_for_prompt,
    select_compact_evidence,
)
from .llm_client import chat_completion, get_model_id, get_provider_name, safe_parse_json
from .prompt_templates import (
    DIAGNOSIS_SYSTEM_PROMPT,
    DIAGNOSIS_TEMPLATE_ID,
    DIAGNOSIS_USER_TEMPLATE,
    build_messages,
    format_kb_for_prompt,
)
from .sanitize import sanitize_evidence_list, sanitize_text

logger = logging.getLogger("diagnosis_engine")

RETRY_EVIDENCE_ITEMS = 8
RETRY_MAX_TOKENS = 3000
PARSE_FAILURE_PHENOMENON = "模型输出格式解析失败"


def _is_parse_failure(answer: dict) -> bool:
    return answer.get("fault_phenomenon") == PARSE_FAILURE_PHENOMENON


def diagnose(
    question: str,
    use_kb: bool = True,
    top_k: int = 4,
    session_id: str | None = None,
    requested_by: str = "web",
) -> dict:
    """
    执行一次完整诊断，返回：
    {
        diagnosis_id, question, model_id, use_kb,
        evidence, retrieved_docs, answer (结构化JSON), response_time_ms
    }
    """
    started_at = time.perf_counter()
    question_sanitized = sanitize_text(question)

    # 1. 识别相关设备
    device_names = extract_relevant_devices(question_sanitized)

    # 2. 取最新采集证据（真实数据，不编造）
    evidence = build_evidence_for_devices(device_names)
    evidence_sanitized = sanitize_evidence_list(evidence)
    evidence_text = format_evidence_for_prompt(evidence_sanitized)

    # 3. 知识库检索（可关闭，用于效果对比测试）
    kb_hits = []
    kb_error = None
    kb_status = "disabled"
    if use_kb:
        try:
            kb_hits = retrieve(question_sanitized, top_k=top_k)
            kb_text = format_kb_for_prompt(kb_hits)
            kb_status = "success" if kb_hits else "empty"
        except Exception as exc:
            # 本地索引缺失时服务仍可用：降级为仅使用已采集的设备证据。
            logger.warning("知识库检索失败，本次诊断降级运行: %s", exc)
            kb_error = f"{type(exc).__name__}: {exc}"
            kb_status = "error"
            kb_text = "（知识库当前不可用，本次诊断已降级为仅使用设备证据）"
    else:
        kb_text = "（本次诊断未启用知识库检索，仅用于效果对比测试）"

    # 4. 组装 Prompt
    messages = build_messages(question_sanitized, evidence_text, kb_text)

    # 5. 调用 LLM；截断或 JSON 解析失败时用精简证据重试一次。
    model_id = get_model_id()
    model_status = "success"
    model_error_code = None
    model_error_message = None
    try:
        content, elapsed_ms, params_used = chat_completion(messages)
        answer = safe_parse_json(content)
        finish_reason = params_used.get("_finish_reason")

        if finish_reason == "length" or _is_parse_failure(answer):
            logger.warning(
                "LLM 输出不可用（finish_reason=%s, parse_failure=%s），使用精简证据重试",
                finish_reason,
                _is_parse_failure(answer),
            )
            retry_evidence = select_compact_evidence(
                evidence_sanitized, limit=RETRY_EVIDENCE_ITEMS
            )
            retry_messages = build_messages(
                question_sanitized,
                format_evidence_for_prompt(retry_evidence),
                kb_text,
            )
            retry_content, retry_elapsed_ms, retry_params = chat_completion(
                retry_messages, max_tokens=RETRY_MAX_TOKENS
            )
            elapsed_ms += retry_elapsed_ms
            answer = safe_parse_json(retry_content)
            params_used = {
                **retry_params,
                "_retry": True,
                "_first_finish_reason": finish_reason,
                "_attempts": 2,
            }
            messages = retry_messages
            finish_reason = retry_params.get("_finish_reason")

        if _is_parse_failure(answer) or finish_reason == "length":
            model_status = "error"
            model_error_code = "LLM_OUTPUT_INVALID"
            model_error_message = (
                "模型输出无法解析或已被截断"
                f"（finish_reason={finish_reason}）"
            )
            error = model_error_message
        else:
            error = None
    except Exception as e:
        logger.error("LLM调用失败: %s", e)
        elapsed_ms = 0
        params_used = {
            "model": model_id,
            "_provider": get_provider_name(),
            "_status": "error",
        }
        model_status = "error"
        model_error_code = type(e).__name__
        model_error_message = str(e)
        answer = {
            "fault_phenomenon": "LLM调用失败",
            "evidence_used": [],
            "possible_causes": [],
            "check_steps": [],
            "fix_suggestions": [],
            "recheck_method": "",
            "missing_information": f"调用大模型接口时发生错误: {type(e).__name__}: {e}",
        }
        error = str(e)

    total_response_time_ms = int((time.perf_counter() - started_at) * 1000)

    # 6. 原子落盘会话、上下文、检索、模型调用、诊断结论和证据引用。
    persisted = db.save_diagnosis_run(
        question=question,
        model_id=model_id,
        prompt_template_id=DIAGNOSIS_TEMPLATE_ID,
        prompt_version={
            "system_prompt": DIAGNOSIS_SYSTEM_PROMPT,
            "user_template": DIAGNOSIS_USER_TEMPLATE,
        },
        llm_params=params_used,
        evidence=evidence_sanitized,  # 脱敏后入库：库内不保留明文凭据
        answer=answer,
        use_kb=1 if use_kb else 0,
        retrieved_docs=kb_hits,
        kb_status=kb_status,
        kb_error=kb_error,
        response_time_ms=elapsed_ms,
        total_response_time_ms=total_response_time_ms,
        context_messages=messages,
        requested_by=requested_by,
        session_id=session_id,
        model_status=model_status,
        model_error_code=model_error_code,
        model_error_message=model_error_message,
        top_k=top_k,
    )
    diagnosis_id = persisted["diagnosis_id"]

    return {
        "diagnosis_id": diagnosis_id,
        "session_id": persisted["session_id"],
        "conclusion_id": persisted["conclusion_id"],
        "context_snapshot_id": persisted["context_snapshot_id"],
        "retrieval_query_id": persisted["retrieval_query_id"],
        "model_call_id": persisted["model_call_id"],
        "question": question,
        "model_id": model_id,
        "prompt_template_id": DIAGNOSIS_TEMPLATE_ID,
        "use_kb": use_kb,
        "evidence": evidence_sanitized,
        "retrieved_docs": kb_hits,
        "knowledge_base": {
            "status": (
                "disabled"
                if not use_kb
                else "ready" if kb_status == "success"
                else "empty" if kb_status == "empty"
                else "error" if kb_error
                else "empty"
            ),
            "error": kb_error,
        },
        "answer": answer,
        "response_time_ms": elapsed_ms,
        "total_response_time_ms": total_response_time_ms,
        "model_status": model_status,
        "model_error_code": model_error_code,
        "model_error_message": model_error_message,
        "error": error,
    }
