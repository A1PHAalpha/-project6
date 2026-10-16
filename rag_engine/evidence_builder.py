"""
证据构建模块：从 SQLite 采集记录中提取与问题相关的设备证据，
统一格式化为 {device_name, collected_at, status, item, summary} 列表，
供 Prompt 组装使用。所有证据都带采集时间，保证"证据=最近一次真实采集"。
"""
import json
from datetime import datetime, timedelta

from collector import db

# 证据过期阈值：超过此时长的采集记录在呈现时会标注"证据可能已过期"
EVIDENCE_STALE_MINUTES = 30


def _is_stale(collected_at: str) -> bool:
    try:
        ts = datetime.fromisoformat(collected_at)
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        return (now - ts) > timedelta(minutes=EVIDENCE_STALE_MINUTES)
    except Exception:
        return False


def build_evidence_for_devices(device_names: list) -> list:
    """
    对给定设备列表，取每个设备各采集项的最新一条记录，组装为证据列表。
    若设备从未采集过，返回 status='no_data' 的占位证据，而不是编造数据。
    """
    evidence = []
    with db.get_conn() as conn:
        for name in device_names:
            rows = conn.execute(
                """SELECT * FROM collection_records cr
                   WHERE cr.device_name = ? AND cr.id IN (
                       SELECT MAX(id) FROM collection_records
                       WHERE device_name = ? GROUP BY command
                   )
                   ORDER BY command""",
                (name, name),
            ).fetchall()

            if not rows:
                evidence.append({
                    "device_name": name,
                    "collected_at": None,
                    "status": "no_data",
                    "command": None,
                    "summary": "该设备尚无任何采集记录，无法提供证据。",
                })
                continue

            for row in rows:
                r = dict(row)
                item = {
                    "device_name": name,
                    "collected_at": r["collected_at"],
                    "status": r["status"],
                    "command": r["command"],
                    "is_stale": _is_stale(r["collected_at"]) if r["status"] == "success" else False,
                }
                if r["status"] == "success":
                    item["summary"] = _summarize(r)
                else:
                    item["summary"] = f"采集失败：{r.get('fail_reason') or '未知原因'}"
                evidence.append(item)

    return evidence


def _summarize(record: dict) -> str:
    """生成简短摘要文本，供 Prompt 中紧凑呈现；完整原始输出仍保留在 raw_output 字段供界面展示。"""
    parsed = {}
    if record.get("parsed_json"):
        try:
            parsed = json.loads(record["parsed_json"])
        except Exception:
            parsed = {}
    cmd = record.get("command", "")
    raw = record.get("raw_output") or ""
    snippet = raw.strip().replace("\r\n", "\n")
    if len(snippet) > 600:
        snippet = snippet[:600] + "...(已截断)"
    return f"[{cmd}] 解析摘要={parsed} 原始输出摘录=\n{snippet}"


def format_evidence_for_prompt(evidence: list) -> str:
    """将证据列表格式化为 Prompt 中可读的文本块。"""
    lines = []
    for e in evidence:
        stale_tag = "（注意：该证据超过30分钟，可能已过期）" if e.get("is_stale") else ""
        lines.append(
            f"- 设备: {e['device_name']} | 采集时间: {e['collected_at'] or '无'} | "
            f"状态: {e['status']}{stale_tag}\n  {e['summary']}"
        )
    if not lines:
        return "（未提供任何设备证据）"
    return "\n".join(lines)
