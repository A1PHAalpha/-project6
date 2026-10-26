"""评测规则和聚合指标测试。"""
from test_cases.evaluation import (
    aggregate_results,
    evidence_hash,
    expected_for_scenario,
    score_answer,
)
from test_cases.scenarios import get_scenario


def test_score_answer_requires_expected_terms_and_devices():
    scenario = get_scenario("DEFAULT_ROUTE_MISSING_01")
    expected = expected_for_scenario(scenario)
    answer = {
        "fault_phenomenon": "BR-B-R3 缺少默认路由，0.0.0.0/0 未配置",
        "possible_causes": [{"cause": "缺省路由丢失"}],
        "fix_suggestions": [
            {
                "action": "ip route 0.0.0.0 0.0.0.0 10.0.0.1",
                "requires_manual_confirmation": True,
            }
        ],
        "recheck_method": "ping 10.10.10.100",
    }
    result = score_answer(answer, expected)
    assert result["correctness"] == "correct"
    assert result["executability_score"] == 1.0


def test_score_answer_marks_forbidden_claim_as_wrong():
    scenario = get_scenario("NORMAL_01")
    expected = expected_for_scenario(scenario)
    result = score_answer(
        {
            "fault_phenomenon": "链路中断，默认路由缺失",
            "fix_suggestions": [],
            "recheck_method": "ping",
        },
        expected,
    )
    assert result["correctness"] == "wrong"


def test_score_answer_marks_model_error():
    scenario = get_scenario("ACL_OVERBLOCK_01")
    expected = expected_for_scenario(scenario)
    result = score_answer(
        {
            "fault_phenomenon": "大模型调用失败",
            "missing_information": "LLM API timeout",
        },
        expected,
    )
    assert result["correctness"] == "error"


def test_normal_accepts_synonyms_for_no_fault():
    scenario = get_scenario("NORMAL_01")
    expected = expected_for_scenario(scenario)
    result = score_answer(
        {
            "fault_phenomenon": "HQ-R1 与分支A之间网络未发现异常",
            "possible_causes": [],
            "fix_suggestions": [],
            "recheck_method": "ping 192.168.200.10",
            "missing_information": "无",
        },
        expected,
    )
    assert result["correctness"] == "correct"


def test_aggregate_and_evidence_hash():
    results = [
        {
            "kb_enabled": True,
            "correctness": "correct",
            "executability_score": 1.0,
            "total_response_time_ms": 100,
        },
        {
            "kb_enabled": False,
            "correctness": "partial",
            "executability_score": 0.5,
            "total_response_time_ms": 50,
        },
    ]
    summary = aggregate_results(results)
    assert summary["by_variant"]["with_kb"]["accuracy"] == 1.0
    assert summary["by_variant"]["without_kb"]["accuracy"] == 0.5
    assert summary["comparison"]["accuracy_delta"] == 0.5
    assert evidence_hash([{"a": 1}]) == evidence_hash([{"a": 1}])
