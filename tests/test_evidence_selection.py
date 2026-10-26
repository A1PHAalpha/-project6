from rag_engine.evidence_builder import (
    MAX_EVIDENCE_ITEMS,
    _observation_priority,
    _summarize_observation,
    select_compact_evidence,
)


def _item(device, priority, state_type="route"):
    return {
        "device_name": device,
        "state_type": state_type,
        "priority": priority,
        "collected_at": "2026-10-26T00:00:00+00:00",
        "summary": f"{device}-{state_type}-{priority}",
    }


def test_compact_evidence_caps_total_and_keeps_each_device():
    evidence = []
    for device in ("HQ-R1", "HQ-SW1", "BR-A-R2"):
        evidence.extend(_item(device, i) for i in range(20))
    selected = select_compact_evidence(evidence, limit=10)
    assert len(selected) == 10
    assert {item["device_name"] for item in selected} == {"HQ-R1", "HQ-SW1", "BR-A-R2"}


def test_compact_evidence_keeps_interface_and_route_diversity():
    evidence = [
        _item("HQ-R1", 2, "route") for _ in range(20)
    ] + [
        _item("HQ-R1", 6, "interface") for _ in range(5)
    ]
    selected = select_compact_evidence(evidence, limit=10)
    state_types = {item["state_type"] for item in selected}
    assert "interface" in state_types
    assert "route" in state_types


def test_compact_evidence_prioritizes_down_interface():
    evidence = [
        _item("HQ-R1", 5, "interface"),
        _item("HQ-R1", 0, "interface"),
        _item("HQ-R1", 3, "route"),
    ]
    selected = select_compact_evidence(evidence, limit=1)
    assert selected[0]["priority"] == 0


def test_compact_evidence_default_cap_is_thirty():
    evidence = [_item("HQ-R1", i % 5) for i in range(100)]
    assert len(select_compact_evidence(evidence)) == MAX_EVIDENCE_ITEMS


def test_system_summary_is_truncated():
    observation = {
        "state_type": "system",
        "entity_key": "snmp_system",
        "payload": {"interfaces": [{"name": "Et0/0", "descr": "x" * 200}] * 50},
    }
    summary = _summarize_observation(observation)
    assert len(summary) <= 300


def test_admin_down_is_lower_priority_than_link_failure():
    link_failure = {
        "state_type": "interface",
        "payload": {"status": "down", "admin_status": "up"},
    }
    admin_down = {
        "state_type": "interface",
        "payload": {"status": "down", "admin_status": "down"},
    }
    route = {"state_type": "route", "payload": {}}
    assert _observation_priority(link_failure) == 0
    assert _observation_priority(admin_down) > _observation_priority(route)


def test_interface_summary_marks_admin_down_as_possible_unused():
    summary = _summarize_observation(
        {
            "state_type": "interface",
            "entity_key": "Ethernet0/1",
            "payload": {"status": "down", "admin_status": "down"},
        }
    )
    assert "可能是未使用端口" in summary
