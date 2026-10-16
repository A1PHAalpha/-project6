"""
RAG 故障诊断引擎主流程：
问题输入 -> 识别相关设备 -> 取最新采集证据 -> 向量检索知识库 -> 脱敏 ->
组装Prompt -> 调用LLM -> 解析结构化结果 -> 落盘诊断记录（含模型标识/模板/参数/检索来源）。

支持 use_kb=False 模式用于效果测试环节的"无知识库检索"对比。
"""
import logging

from collector import db
from knowledge_base.retriever import retrieve

from .device_extractor import extract_relevant_devices
from .evidence_builder import build_evidence_for_devices, format_evidence_for_prompt
from .llm_client import chat_completion, get_model_id, safe_parse_json
from .prompt_templates import DIAGNOSIS_TEMPLATE_ID, build_messages, format_kb_for_prompt
from .sanitize import sanitize_evidence_list, sanitize_text

logger = logging.getLogger("diagnosis_engine")


def diagnose(question: str, use_kb: bool = True, top_k: int = 4) -> dict:
    """
    执行一次完整诊断，返回：
    {
        diagnosis_id, question, model_id, use_kb,
        evidence, retrieved_docs, answer (结构化JSON), response_time_ms
    }
    """
    # 1. 识别相关设备
    device_names = extract_relevant_devices(question)

    # 2. 取最新采集证据（真实数据，不编造）
    evidence = build_evidence_for_devices(device_names)
    evidence_sanitized = sanitize_evidence_list(evidence)
    evidence_text = format_evidence_for_prompt(evidence_sanitized)

    # 3. 知识库检索（可关闭，用于效果对比测试）
    kb_hits = retrieve(question, top_k=top_k) if use_kb else []
    kb_text = format_kb_for_prompt(kb_hits) if use_kb else "（本次诊断未启用知识库检索，仅用于效果对比测试）"

    # 4. 组装 Prompt
    question_sanitized = sanitize_text(question)
    messages = build_messages(question_sanitized, evidence_text, kb_text)

    # 5. 调用 LLM
    model_id = get_model_id()
    try:
        content, elapsed_ms, params_used = chat_completion(messages)
        answer = safe_parse_json(content)
        error = None
    except Exception as e:
        logger.error("LLM调用失败: %s", e)
        elapsed_ms = 0
        params_used = {"model": model_id}
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

    # 6. 落盘诊断记录（可核查：模型标识/模板/参数/检索来源/证据时间）
    retrieved_doc_ids = [h["id"] for h in kb_hits]
    diagnosis_id = db.insert_diagnosis(
        question=question,
        model_id=model_id,
        prompt_template_id=DIAGNOSIS_TEMPLATE_ID,
        llm_params=params_used,
        retrieved_doc_ids=retrieved_doc_ids,
        evidence=evidence_sanitized,  # 脱敏后入库：库内不保留明文凭据
        answer=answer,
        use_kb=1 if use_kb else 0,
        response_time_ms=elapsed_ms,
    )

    return {
        "diagnosis_id": diagnosis_id,
        "question": question,
        "model_id": model_id,
        "prompt_template_id": DIAGNOSIS_TEMPLATE_ID,
        "use_kb": use_kb,
        "evidence": evidence_sanitized,
        "retrieved_docs": kb_hits,
        "answer": answer,
        "response_time_ms": elapsed_ms,
        "error": error,
    }
