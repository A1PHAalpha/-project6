"""
从自然语言问题中提取涉及的设备/站点关键词，映射到设备台账中的具体设备名。
采用简单关键词匹配（而非LLM NER），足够应对课题的固定拓扑场景，且无需额外模型调用。
"""
from collector import db

SITE_KEYWORDS = {
    "总部": ["总部", "HQ", "本部"],
    "分支A": ["分支a", "分支1", "分支一", "branch a", "分公司a"],
    "分支B": ["分支b", "分支2", "分支二", "branch b", "分公司b"],
}


def extract_relevant_devices(question: str) -> list:
    """
    返回与问题相关的设备名列表。若无法从问题中识别出具体站点，
    则返回全部设备（保证诊断引擎至少有证据可用，而不是直接返回空）。
    """
    q = question.lower()
    matched_sites = []
    for site, keywords in SITE_KEYWORDS.items():
        if any(kw.lower() in q for kw in keywords):
            matched_sites.append(site)

    devices = db.list_devices()
    if not matched_sites:
        return [d["device_name"] for d in devices]

    result = [d["device_name"] for d in devices if d["site"] in matched_sites]
    return result if result else [d["device_name"] for d in devices]
