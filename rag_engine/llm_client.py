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

from openai import OpenAI

logger = logging.getLogger("llm_client")

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        if not api_key:
            raise RuntimeError("未配置 LLM_API_KEY，请检查 .env 文件")
        _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def get_model_id() -> str:
    return os.getenv("LLM_MODEL", "qwen-plus")


def chat_completion(messages: list, temperature: float = 0.2, max_tokens: int = 1500,
                     response_format_json: bool = True):
    """
    调用 LLM，返回 (content_text, elapsed_ms, params_used)。
    response_format_json: 若模型支持，要求返回 JSON 格式。
    """
    client = get_client()
    model = get_model_id()
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

    content = resp.choices[0].message.content
    return content, elapsed_ms, {"model": model, **params}


def safe_parse_json(text: str) -> dict:
    """尝试解析模型输出为 JSON；若失败则包装为错误结构，不让异常直接中断诊断流程。"""
    try:
        return json.loads(text)
    except Exception:
        # 尝试提取第一个 { 到最后一个 } 之间的内容
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass
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
