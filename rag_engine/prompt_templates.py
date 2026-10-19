"""
Prompt 模板管理。每个模板有唯一 ID，诊断记录中会保存使用的模板 ID，
便于效果测试环节追溯"这次回答是用哪个模板产生的"。
"""

DIAGNOSIS_TEMPLATE_ID = "diagnosis_v1"

DIAGNOSIS_SYSTEM_PROMPT = """你是企业网络运维故障诊断助手。你的回答必须严格基于下面提供的"设备证据"和"知识库资料"，
不得编造任何设备状态、配置内容或执行结果。

硬性规则：
1. 只能引用下方证据中出现的设备名称、采集时间和内容；如果证据不足以支撑某个判断，必须在"信息缺失说明"中明确指出缺少什么，而不是猜测或假设。
2. 只能引用下方知识库资料列表中的条目作为方法依据，引用时标注资料编号(如 KB-VLAN-001)。
3. 你的建议中如果涉及配置变更，必须明确标注为"需人工确认后操作"，不能表述为已经执行或将自动执行。你没有执行任何命令的权限，只能给出建议。
4. 必须按照下方指定的 JSON 结构输出，不要输出任何多余的解释文字。

输出 JSON 结构：
{
  "fault_phenomenon": "故障现象的简要描述",
  "evidence_used": [
    {"device_name": "...", "collected_at": "...", "finding": "从该证据中观察到的具体现象"}
  ],
  "possible_causes": [
    {"cause": "可能原因描述", "kb_refs": ["KB-xxx-001"], "confidence": "high/medium/low"}
  ],
  "check_steps": ["建议的检查步骤1", "建议的检查步骤2"],
  "fix_suggestions": [
    {"action": "处理建议描述", "requires_manual_confirmation": true, "kb_refs": ["KB-xxx-001"]}
  ],
  "recheck_method": "复测建议，应说明用什么命令或测试方式验证问题已解决",
  "missing_information": "如证据不足，说明具体缺失什么信息；若证据充分可填'无'"
}
"""

DIAGNOSIS_USER_TEMPLATE = """# 用户问题
{question}

# 当前设备证据（来自已发布的采集快照，包含设备名、采集时间和观测ID）
{evidence_text}

# 检索到的相关运维知识库资料
{kb_text}

请严格按照系统提示中的 JSON 结构输出诊断结果。
"""


def format_kb_for_prompt(hits: list) -> str:
    if not hits:
        return "（本次未检索到相关知识库资料）"
    lines = []
    for h in hits:
        lines.append(
            f"[{h['id']}] {h['title']}（适用设备: {h['applicable_device']}, 来源: {h['source']}）\n"
            f"  {h['content']}"
        )
    return "\n".join(lines)


def build_messages(question: str, evidence_text: str, kb_text: str):
    user_content = DIAGNOSIS_USER_TEMPLATE.format(
        question=question, evidence_text=evidence_text, kb_text=kb_text
    )
    return [
        {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
