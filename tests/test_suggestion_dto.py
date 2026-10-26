from rag_engine.prompt_templates import DIAGNOSIS_TEMPLATE_ID
from webapp.api.v1.router import _suggestion_dto


def test_suggestion_dto_extracts_action_command_and_target():
    answer = {
        "fix_suggestions": [
            {
                "action": "检查互联链路",
                "command": "show interfaces GigabitEthernet0/1",
                "target_device": "BR-A-R2",
                "requires_manual_confirmation": True,
            }
        ],
        "check_steps": ["show cdp neighbors"],
    }
    result = _suggestion_dto(answer, [{"device_name": "BR-A-R2"}])
    assert result["command"] == ["show interfaces GigabitEthernet0/1"]
    assert result["actions"] == ["检查互联链路"]
    assert result["targetDevice"] == "BR-A-R2"
    assert result["canExecute"] is True


def test_legacy_suggestion_without_command_is_display_only():
    answer = {
        "fix_suggestions": [
            {"action": "人工核对默认路由", "requires_manual_confirmation": True}
        ]
    }
    result = _suggestion_dto(answer, [{"device_name": "BR-B-R3"}])
    assert result["command"] == []
    assert result["actions"] == ["人工核对默认路由"]
    assert result["targetDevice"] == "BR-B-R3"
    assert result["canExecute"] is False


def test_prompt_template_is_v2_and_requests_command_target():
    assert DIAGNOSIS_TEMPLATE_ID == "diagnosis_v2"
    from rag_engine.prompt_templates import DIAGNOSIS_SYSTEM_PROMPT

    assert '"command"' in DIAGNOSIS_SYSTEM_PROMPT
    assert '"target_device"' in DIAGNOSIS_SYSTEM_PROMPT


def test_prompt_bounds_evidence_and_command_output():
    from rag_engine.prompt_templates import DIAGNOSIS_SYSTEM_PROMPT

    assert "最多 5 条" in DIAGNOSIS_SYSTEM_PROMPT
    assert "不要逐条复述全部证据" in DIAGNOSIS_SYSTEM_PROMPT
    assert "单条命令" in DIAGNOSIS_SYSTEM_PROMPT
    assert "不要输出多行" in DIAGNOSIS_SYSTEM_PROMPT
