"""
报告导出模块：将单次诊断记录（含证据、检索依据、诊断建议、人工处理记录）
导出为 Markdown 报告文件，满足方案"支持导出报告"的要求。
"""
import json
import os
from pathlib import Path

from collector import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = str(PROJECT_ROOT / "reports")


def export_diagnosis_report(diagnosis_id: int):
    record = db.get_diagnosis(diagnosis_id)
    if not record:
        return None

    actions = db.list_manual_actions(diagnosis_id=diagnosis_id)
    answer = json.loads(record["answer_json"])
    evidence = json.loads(record["evidence_json"])
    retrieved = json.loads(record["retrieved_doc_ids_json"])
    params = json.loads(record["llm_params_json"])

    lines = []
    lines.append(f"# 故障诊断报告 #{diagnosis_id}")
    lines.append("")
    lines.append(f"- 诊断时间: {record['created_at']}")
    lines.append(f"- 模型标识: {record['model_id']}")
    lines.append(f"- Prompt模板: {record['prompt_template_id']}")
    lines.append(f"- 模型参数: {params}")
    lines.append(f"- 是否启用知识库检索: {'是' if record['use_kb'] else '否'}")
    lines.append(f"- 响应耗时: {record['response_time_ms']} ms")
    lines.append(f"- 检索到的知识库条目: {', '.join(retrieved) if retrieved else '无'}")
    lines.append("")
    lines.append("## 用户问题")
    lines.append(record["question"])
    lines.append("")
    lines.append("## 设备证据（采集时间+状态）")
    for e in evidence:
        lines.append(f"- 设备: {e['device_name']} | 采集时间: {e.get('collected_at') or '无'} | 状态: {e['status']}")
    lines.append("")
    lines.append("## 故障现象")
    lines.append(answer.get("fault_phenomenon", ""))
    lines.append("")
    lines.append("## 可能原因")
    for c in answer.get("possible_causes", []):
        lines.append(f"- {c.get('cause')} (依据: {', '.join(c.get('kb_refs', []))}, 置信度: {c.get('confidence')})")
    lines.append("")
    lines.append("## 检查步骤")
    for i, step in enumerate(answer.get("check_steps", []), 1):
        lines.append(f"{i}. {step}")
    lines.append("")
    lines.append("## 处理建议")
    for s in answer.get("fix_suggestions", []):
        lines.append(f"- {s.get('action')} (需人工确认: {s.get('requires_manual_confirmation')}, 依据: {', '.join(s.get('kb_refs', []))})")
    lines.append("")
    lines.append("## 复测方法")
    lines.append(answer.get("recheck_method", ""))
    lines.append("")
    lines.append("## 信息缺失说明")
    lines.append(answer.get("missing_information", "无"))
    lines.append("")
    lines.append("## 人工处理记录")
    if actions:
        for a in actions:
            lines.append(f"- [{a['created_at']}] 操作人: {a['operator']} | 状态: {a['status']}")
            lines.append(f"  操作描述: {a['action_desc']}")
            if a.get("backup_ref"):
                lines.append(f"  备份文件: {a['backup_ref']}")
            if a.get("recheck_result"):
                lines.append(f"  复测结果: {a['recheck_result']}")
    else:
        lines.append("（暂无人工处理记录）")

    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"diagnosis_{diagnosis_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return path
