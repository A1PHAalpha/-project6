"""safe_parse_json 三级降级单测。"""
import json

from rag_engine.llm_client import safe_parse_json


def test_valid_json():
    assert safe_parse_json('{"a": 1}') == {"a": 1}


def test_json_with_surrounding_noise():
    out = safe_parse_json('模型输出如下： {"a": 2, "b": 3} 以上。')
    assert out == {"a": 2, "b": 3}


def test_unparseable_returns_placeholder():
    out = safe_parse_json("这完全不是JSON")
    assert out["fault_phenomenon"] == "模型输出格式解析失败"
    assert "check_steps" in out


def test_safe_parse_json_normalizes_text_and_list_fields():
    payload = {
        "fault_phenomenon": ["链路正常", "未发现异常"],
        "evidence_used": [
            {
                "observation_id": "obs_1",
                "device_name": "HQ-R1",
                "finding": ["接口 up"],
            }
        ],
        "possible_causes": ["无明确故障原因"],
        "check_steps": "show ip interface brief",
        "fix_suggestions": [
            {
                "action": "继续观察",
                "command": ["show ip interface brief"],
                "kb_refs": "KB-GEN-001",
            }
        ],
        "recheck_method": ["ping 10.10.10.100"],
        "missing_information": ["缺少日志", "缺少 CPU 指标"],
    }
    out = safe_parse_json(json.dumps(payload, ensure_ascii=False))

    assert isinstance(out["fault_phenomenon"], str)
    assert out["fault_phenomenon"] == "链路正常；未发现异常"
    assert out["missing_information"] == "缺少日志；缺少 CPU 指标"
    assert out["check_steps"] == ["show ip interface brief"]
    assert out["possible_causes"][0]["cause"] == "无明确故障原因"
    assert out["possible_causes"][0]["kb_refs"] == []
    assert out["fix_suggestions"][0]["kb_refs"] == ["KB-GEN-001"]
    assert isinstance(out["fix_suggestions"][0]["command"], str)
    assert out["evidence_used"][0]["finding"] == "接口 up"
