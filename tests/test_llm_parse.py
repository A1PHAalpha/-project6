"""safe_parse_json 三级降级单测。"""
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
