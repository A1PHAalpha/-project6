"""
从自然语言问题中提取涉及的设备/站点关键词，映射到设备台账中的具体设备名。
优先精确匹配设备名和管理 IP；未命中时再回退到站点关键词。
"""
import re

from collector import db

SITE_KEYWORDS = {
    "总部": ["总部", "HQ", "本部"],
    "分支A": ["分支a", "分支1", "分支一", "branch a", "分公司a"],
    "分支B": ["分支b", "分支2", "分支二", "branch b", "分公司b"],
}


def _contains_device_name(question: str, name: str) -> bool:
    if not name:
        return False
    escaped = re.escape(name.lower())
    return re.search(
        rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", question.lower()
    ) is not None


def _contains_ip(question: str, ip: str) -> bool:
    if not ip:
        return False
    return re.search(rf"(?<!\d){re.escape(ip)}(?!\d)", question) is not None


def _active_devices() -> list[dict]:
    return [
        device
        for device in db.list_devices()
        if device.get("lifecycle_status") != "retired"
    ]


def extract_relevant_devices(question: str) -> list:
    """
    返回与问题相关的设备名列表。

    匹配顺序：
    1. 设备名精确词匹配；
    2. 管理 IP 精确匹配；
    3. 站点关键词匹配；
    4. 无法识别时返回全部未退役设备。
    """
    devices = _active_devices()
    if not devices:
        return []

    exact_matches = [
        device["device_name"]
        for device in devices
        if _contains_device_name(question, device.get("device_name") or "")
        or _contains_ip(question, device.get("mgmt_ip") or "")
    ]
    if exact_matches:
        return list(dict.fromkeys(exact_matches))

    q = question.lower()
    matched_sites = [
        site
        for site, keywords in SITE_KEYWORDS.items()
        if any(keyword.lower() in q for keyword in keywords)
    ]
    if matched_sites:
        site_devices = [
            device["device_name"]
            for device in devices
            if device.get("site") in matched_sites
        ]
        if site_devices:
            return site_devices

    return [device["device_name"] for device in devices]
