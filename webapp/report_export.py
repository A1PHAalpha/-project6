"""
报告导出模块：将单次诊断记录（含证据、检索依据、诊断建议、人工处理记录）
导出为 Markdown 或可打印的 HTML 报告文件。
"""
import html
import json
import os
import re
from pathlib import Path

from collector import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = str(PROJECT_ROOT / "reports")


def _build_report_markdown(diagnosis_id: int) -> str | None:
    detail = db.get_diagnosis_detail(diagnosis_id)
    if not detail:
        return None
    record = detail["diagnosis"]

    actions = db.list_manual_actions(diagnosis_id=diagnosis_id)
    answer = json.loads(record["answer_json"] or "{}")
    evidence = json.loads(record["evidence_json"] or "[]")
    retrieved_docs = detail.get("retrieved_docs") or []
    retrieved = [doc["id"] for doc in retrieved_docs]
    if not retrieved:
        retrieved = json.loads(record["retrieved_doc_ids_json"] or "[]")
    params = json.loads(record["llm_params_json"] or "{}")
    session = detail.get("session") or {}
    session_record = session.get("session") or {}

    lines = []
    lines.append(f"# 故障诊断报告 #{diagnosis_id}")
    lines.append("")
    lines.append(f"- 诊断时间: {record['created_at']}")
    lines.append(f"- 模型标识: {record['model_id']}")
    lines.append(f"- Prompt模板: {record['prompt_template_id']}")
    lines.append(f"- 模型参数: {params}")
    lines.append(f"- 是否启用知识库检索: {'是' if record['use_kb'] else '否'}")
    lines.append(f"- 知识库检索状态: {record.get('kb_status') or '未记录'}")
    lines.append(f"- 响应耗时: {record['response_time_ms']} ms")
    lines.append(f"- 全链路耗时: {record.get('total_response_time_ms') or '未记录'} ms")
    lines.append(f"- 会话 ID: {record.get('session_id') or '未记录'}")
    lines.append(f"- 诊断结论 ID: {record.get('conclusion_id') or '未记录'}")
    lines.append(f"- 检索到的知识库条目: {', '.join(retrieved) if retrieved else '无'}")
    lines.append("")
    lines.append("## 用户问题")
    lines.append(record["question"])
    lines.append("")
    lines.append("## 设备证据（采集时间+状态）")
    for e in evidence:
        lines.append(f"- 设备: {e['device_name']} | 采集时间: {e.get('collected_at') or '无'} | 状态: {e['status']}")
        lines.append(f"  观测 ID: {e.get('observation_id') or '无'} | 采集批次: {e.get('run_id') or '无'}")
        lines.append(f"  {e.get('summary') or ''}")
    lines.append("")
    lines.append("## 知识库检索快照")
    if retrieved_docs:
        for doc in retrieved_docs:
            lines.append(
                f"- [{doc['id']}] {doc.get('title') or ''} | 排名: {doc.get('rank')} | "
                f"距离: {doc.get('distance')} | 版本: {doc.get('version') or '未记录'} | "
                f"来源: {doc.get('source') or '未记录'}"
            )
            lines.append(f"  内容哈希: {doc.get('content_hash') or '未记录'}")
    else:
        lines.append("（本次未检索到资料）")
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
    lines.append("")
    lines.append("## 人工变更与复测")
    changes = detail.get("changes") or []
    if changes:
        for change_row in changes:
            change_detail = db.get_change(change_row["change_id"])
            if not change_detail:
                continue
            change = change_detail["change"]
            lines.append(
                f"### {change['title']}（{change['change_id']}）"
            )
            lines.append(
                f"- 设备: {change['device_name']} | 类型: {change['change_type']} | "
                f"状态: {change['status']} | 风险: {change['risk_level']}"
            )
            lines.append(
                f"- 申请人: {change['requested_by']} | 审批人: {change['approved_by'] or '未审批'}"
            )
            if change.get("description"):
                lines.append(f"- 说明: {change['description']}")
            if change.get("rollback_plan"):
                lines.append(f"- 回滚方案: {change['rollback_plan']}")
            for step in change_detail["steps"]:
                lines.append(
                    f"- 步骤 #{step['seq_no']} [{step['step_type']}/{step['source_type']}] "
                    f"{step['title']} | 状态: {step['status']}"
                )
                if step.get("command_text"):
                    lines.append(f"  计划命令: {step['command_text']}")
                if step.get("result_summary"):
                    lines.append(f"  结果: {step['result_summary']}")
            for execution in change_detail["command_executions"]:
                lines.append(
                    f"- 实际命令: {execution['command_text']} | 执行人: {execution['executed_by']} "
                    f"| 退出码: {execution.get('exit_code')}"
                )
                if execution.get("stdout_text"):
                    lines.append(f"  输出: {execution['stdout_text'][:500]}")
            for physical in change_detail["physical_actions"]:
                lines.append(
                    f"- 物理操作: {physical['action_type']} | {physical['description']} "
                    f"| 操作人: {physical['performed_by']} | 结果: {physical.get('result') or '未记录'}"
                )
            for backup in change_detail["config_backups"]:
                lines.append(
                    f"- 配置备份: {backup['backup_type']} | {backup.get('storage_ref') or '无路径'} "
                    f"| sha256: {backup.get('sha256') or '未记录'}"
                )
            for recheck in change_detail["rechecks"]:
                lines.append(
                    f"- 复测: {recheck['check_type']} {recheck.get('source_device_name')} -> "
                    f"{recheck['target']} | {recheck['pass_fail']} | 批次: "
                    f"{recheck.get('collection_run_id') or '无'}"
                )
            for event in change_detail["events"]:
                lines.append(
                    f"- 事件 [{event['created_at_utc']}] {event['event_type']} | "
                    f"{event['actor']} | {event.get('from_status') or '无'} -> "
                    f"{event.get('to_status') or '无'}"
                )
    else:
        lines.append("（暂无人工变更记录）")
    lines.append("")
    lines.append("## 会话审计")
    if session_data := session.get("messages"):
        for message in session_data:
            role = message.get("role")
            content = (message.get("content_text") or "").replace("\n", " ")
            lines.append(f"- [{message.get('created_at_utc')}] {role}: {content[:300]}")
    else:
        lines.append("（旧记录未保存完整会话消息）")
    if session_record.get("prompt_version_id"):
        lines.append("")
        lines.append(f"- Prompt 版本: {session_record['prompt_version_id']}")

    return "\n".join(lines)


def _write_report(content: str, diagnosis_id: int, extension: str) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"diagnosis_{diagnosis_id}.{extension}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def export_diagnosis_report(diagnosis_id: int):
    content = _build_report_markdown(diagnosis_id)
    if content is None:
        return None
    return _write_report(content, diagnosis_id, "md")


def _markdown_to_html(markdown: str, title: str) -> str:
    body: list[str] = []
    in_ul = False
    in_ol = False

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            body.append("</ul>")
            in_ul = False
        if in_ol:
            body.append("</ol>")
            in_ol = False

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("### "):
            close_lists()
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("## "):
            close_lists()
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            close_lists()
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("- "):
            if in_ol:
                body.append("</ol>")
                in_ol = False
            if not in_ul:
                body.append("<ul>")
                in_ul = True
            body.append(f"<li>{html.escape(line[2:])}</li>")
        elif re.match(r"^\d+\.\s+", line):
            if in_ul:
                body.append("</ul>")
                in_ul = False
            if not in_ol:
                body.append("<ol>")
                in_ol = True
            item = re.sub(r"^\d+\.\s+", "", line)
            body.append(f"<li>{html.escape(item)}</li>")
        elif not line.strip():
            close_lists()
        else:
            close_lists()
            body.append(f"<p>{html.escape(line)}</p>")
    close_lists()

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{
    margin: 0 auto;
    max-width: 960px;
    padding: 32px 24px 64px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC",
      "Microsoft YaHei", sans-serif;
    line-height: 1.65;
    color: #1f2937;
    background: #ffffff;
  }}
  h1 {{ font-size: 1.8rem; border-bottom: 2px solid #0f766e; padding-bottom: 8px; }}
  h2 {{ font-size: 1.25rem; margin-top: 28px; color: #0f766e; }}
  h3 {{ font-size: 1.05rem; margin-top: 20px; }}
  p, li {{ font-size: 0.95rem; }}
  li {{ margin: 4px 0; }}
  @media print {{
    body {{ max-width: none; padding: 0; }}
    h2 {{ break-after: avoid; }}
    li, p {{ break-inside: avoid; }}
    a {{ color: inherit; text-decoration: none; }}
  }}
</style>
</head>
<body>
{chr(10).join(body)}
</body>
</html>
"""


def export_diagnosis_report_html(diagnosis_id: int):
    content = _build_report_markdown(diagnosis_id)
    if content is None:
        return None
    title = f"故障诊断报告 #{diagnosis_id}"
    return _write_report(
        _markdown_to_html(content, title), diagnosis_id, "html"
    )
