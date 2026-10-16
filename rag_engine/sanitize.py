"""
脱敏模块：在把设备证据/配置文本发送给外部 LLM API、以及写入数据库之前，
剔除账号、密码、SNMP社区字符串、enable密钥等敏感信息。

规则越具体越靠前，避免被后面的泛化规则提前匹配导致漏脱敏。
"""
import re

SENSITIVE_PATTERNS = [
    # —— 具体字段优先 ——
    (re.compile(r"(crypto\s+isakmp\s+key\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(pre-shared-key\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(wpa-psk\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(key-string\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"((?:tacacs|radius)-server[^\n]*?\bkey\s+(?:[0-9]+[ \t]+)?)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(neighbor\s+\S+\s+password\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(ppp\s+chap\s+password\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    # Cisco 加密类型标识（如 password 7 <hash> / enable secret 5 <hash>），仅用空格/制表符分隔，避免跨行误伤
    (re.compile(r"(username\s+\S+\s+privilege\s+\d+\s+password\s+(?:[0-9]+[ \t]+)?)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(username\s+\S+\s+password\s+(?:[0-9]+[ \t]+)?)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(enable\s+(?:password|secret)\s+(?:[0-9]+[ \t]+)?)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    # 跨行匹配 line vty 配置块内的 password
    (re.compile(r"(line\s+vty.*?\spassword\s+(?:[0-9]+[ \t]+)?)\S+", re.IGNORECASE | re.DOTALL), r"\1[REDACTED]"),
    # —— 泛化规则兜底 ——
    (re.compile(r"(snmp-server\s+community\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(community\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(password\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(secret\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
]


def sanitize_text(text: str) -> str:
    """对单段文本做脱敏替换，返回处理后的文本。"""
    if not text:
        return text
    result = text
    for pattern, repl in SENSITIVE_PATTERNS:
        result = pattern.sub(repl, result)
    return result


def sanitize_evidence_list(evidence: list) -> list:
    """对证据列表中的 summary 字段做脱敏，返回新列表（不修改原数据）。"""
    sanitized = []
    for e in evidence:
        e2 = dict(e)
        if e2.get("summary"):
            e2["summary"] = sanitize_text(e2["summary"])
        sanitized.append(e2)
    return sanitized
