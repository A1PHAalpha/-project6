"""真机故障注入脚本的断言解析与案例表一致性测试（不连接设备）。"""
import pytest

from scripts import live_fault_experiments as live
from test_cases.evaluation import LIVE_EXPECTED_BY_ID, expected_for_live_case

IFACE_BRIEF_DOWN = """Interface              IP-Address      OK? Method Status                Protocol
Ethernet0/0            unassigned      YES unset  administratively down down
Ethernet0/1            unassigned      YES unset  up                    up
"""

IFACE_BRIEF_UP = """Interface              IP-Address      OK? Method Status                Protocol
Ethernet0/0            unassigned      YES unset  up                    up
"""

VLAN_BRIEF_INJECTED = """VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Et0/1, Et0/2, Et0/3, Et2/0, Et3/0
10   USERS                            active
20   SERVERS                          active    Et1/0
99   MGMT                             active
999  MGMT-MGMT                        active
"""

VLAN_BRIEF_RECOVERED = VLAN_BRIEF_INJECTED.replace(
    "20   SERVERS                          active    Et1/0",
    "20   SERVERS                          active",
).replace(
    "10   USERS                            active",
    "10   USERS                            active    Et1/0",
)

TRUNK_BEFORE = """Port        Mode             Encapsulation  Status        Native vlan
Et0/0       on               802.1q         trunking      1

Port        Vlans allowed on trunk
Et0/0       1-4094

Port        Vlans allowed and active in management domain
Et0/0       1,10,20,99,999

Port        Vlans in spanning tree forwarding state and not pruned
Et0/0       1,10,20,99,999
"""

TRUNK_AFTER = TRUNK_BEFORE.replace("Et0/0       1-4094", "Et0/0       1-9,11-4094")

TRUNK_EMPTY = """Port        Mode             Encapsulation  Status        Native vlan

Port        Vlans allowed on trunk

Port        Vlans allowed and active in management domain

Port        Vlans in spanning tree forwarding state and not pruned
"""

ACL_WITH_DENY = """Extended IP access list WAN-IN
    5 deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255 (12 matches)
    10 permit ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255
    50 permit ip any any
"""

ACL_WITHOUT_DENY = """Extended IP access list WAN-IN
    10 permit ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255
    50 permit ip any any
"""


def test_case_table_matches_expected_table():
    ids = [case["id"] for case in live.CASES]
    assert len(ids) == len(set(ids)) == 10
    assert set(ids) == set(LIVE_EXPECTED_BY_ID)
    live.validate_case_table()


def test_eight_fault_cases_have_scoring_keywords():
    fault_cases = [case for case in live.CASES if case.get("inject")]
    assert len(fault_cases) == 8
    for case in fault_cases:
        expected = expected_for_live_case(case["id"])
        assert expected["must_mention"] or expected["must_mention_any"]
        assert expected["expected_devices"]
        assert case["repair"], f"{case['id']} 缺少逆操作"
        assert case["fault_checks"], f"{case['id']} 缺少故障断言"
        assert case["recovery_checks"], f"{case['id']} 缺少恢复断言"


def test_expected_keywords_use_live_topology_naming():
    joined = " ".join(
        " ".join(entry.get("must_mention") or [])
        + " "
        + " ".join(entry.get("expected_devices") or [])
        + " "
        + " ".join(entry.get("must_mention_any") or [])
        for entry in LIVE_EXPECTED_BY_ID.values()
    )
    assert "Gi0/" not in joined
    assert "GigabitEthernet" not in joined
    assert "192.168." not in joined


def test_guard_commands_rejects_persistence_and_mgmt_interfaces():
    for command in ("write memory", "copy run start", "interface Ethernet3/0"):
        with pytest.raises(live.UnsafeCommandError):
            live.guard_commands([command], purpose="pytest")
    live.guard_commands(
        ["interface Ethernet0/0", "shutdown", "no shutdown"], purpose="pytest"
    )


def test_ping_rate_parsing():
    assert live._parse_ping_rate("Success rate is 100 percent (4/4), round-trip min/avg/max = 1/1/1 ms") == 100
    assert live._parse_ping_rate("Success rate is 0 percent (0/4)") == 0
    assert live._parse_ping_rate("% Unrecognized command") is None


def test_interface_line_assertions():
    spec = {
        "type": "line",
        "prefix": "Ethernet0/0",
        "contains": ["administratively down"],
        "not_contains": [" up "],
    }
    ok, _ = live.evaluate_check(spec, IFACE_BRIEF_DOWN)
    assert ok is True
    ok, _ = live.evaluate_check(spec, IFACE_BRIEF_UP)
    assert ok is False

    recovered = {
        "type": "line",
        "prefix": "Ethernet0/0",
        "contains": ["up"],
        "not_contains": ["administratively down"],
    }
    assert live.evaluate_check(recovered, IFACE_BRIEF_UP)[0] is True
    assert live.evaluate_check(recovered, IFACE_BRIEF_DOWN)[0] is False


def test_vlan_port_membership_detection():
    moved = {
        "type": "vlan_ports",
        "vlan_id": 10,
        "port": "Ethernet1/0",
        "expect_member": False,
    }
    assert live.evaluate_check(moved, VLAN_BRIEF_INJECTED)[0] is True
    assert live.evaluate_check(moved, VLAN_BRIEF_RECOVERED)[0] is False

    in_vlan_20 = {
        "type": "vlan_ports",
        "vlan_id": 20,
        "port": "Ethernet1/0",
        "expect_member": True,
    }
    assert live.evaluate_check(in_vlan_20, VLAN_BRIEF_INJECTED)[0] is True
    assert live.evaluate_check(in_vlan_20, VLAN_BRIEF_RECOVERED)[0] is False


def test_trunk_allowed_vlan_detection():
    removed = {
        "type": "trunk_vlans",
        "port": "Ethernet0/0",
        "vlan_id": 10,
        "expect_allowed": False,
    }
    assert live.evaluate_check(removed, TRUNK_AFTER)[0] is True
    assert live.evaluate_check(removed, TRUNK_BEFORE)[0] is False
    assert live.evaluate_check(removed, TRUNK_EMPTY)[0] is False

    restored = dict(removed, expect_allowed=True)
    assert live.evaluate_check(restored, TRUNK_BEFORE)[0] is True
    assert live.evaluate_check(restored, TRUNK_AFTER)[0] is False


def test_vlan_range_membership():
    assert live._vlan_in_list("1-4094", 10) is True
    assert live._vlan_in_list("1-9,11-4094", 10) is False
    assert live._vlan_in_list("1-9,11-4094", 11) is True
    assert live._vlan_in_list("10", 10) is True


def test_acl_text_assertions():
    deny_present = {
        "type": "text",
        "contains": ["5 deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255"],
    }
    assert live.evaluate_check(deny_present, ACL_WITH_DENY)[0] is True
    assert live.evaluate_check(deny_present, ACL_WITHOUT_DENY)[0] is False

    deny_absent = {
        "type": "text",
        "not_contains": ["deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255"],
        "contains": ["permit ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255"],
    }
    assert live.evaluate_check(deny_absent, ACL_WITHOUT_DENY)[0] is True
    assert live.evaluate_check(deny_absent, ACL_WITH_DENY)[0] is False


def test_trunk_port_absence_check_for_link_down():
    spec = {"type": "text", "not_contains": ["Et0/0"]}
    assert live.evaluate_check(spec, TRUNK_EMPTY)[0] is True
    assert live.evaluate_check(spec, TRUNK_BEFORE)[0] is False


class FakeLog:
    def __init__(self, path: str = "fake.log"):
        self.events = []
        self.path = path

    def event(self, level, case_id, step, message):
        self.events.append((level, case_id, step, message))


def test_polling_constants_match_runbook_expectations():
    assert live.PING_POLL_INTERVAL_SECONDS == 5
    assert live.RECHECK_POLL_TIMEOUT_SECONDS == 75
    assert live.SHOW_RECHECK_POLL_TIMEOUT_SECONDS >= 30


def test_poll_ping_keeps_polling_until_success(monkeypatch):
    outputs = iter(
        [
            "Success rate is 0 percent (0/4)",
            "Success rate is 0 percent (0/4)",
            "Success rate is 100 percent (4/4), round-trip min/avg/max = 1/1/1 ms",
        ]
    )
    sleeps = []
    monkeypatch.setattr(live, "send_show", lambda *args, **kwargs: next(outputs))
    monkeypatch.setattr(live, "_sleep", lambda seconds: sleeps.append(seconds))

    record = live.poll_ping(
        live.load_devices(),
        "10.20.10.11",
        expect_success=True,
        case_id="T",
        log=FakeLog(),
        step="recheck",
    )

    assert record["ok"] is True
    assert record["attempt_count"] == 3
    assert record["success_rate"] == 100
    assert sleeps == [live.PING_POLL_INTERVAL_SECONDS, live.PING_POLL_INTERVAL_SECONDS]


def test_poll_ping_times_out_without_success(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(live, "send_show", lambda *args, **kwargs: "Success rate is 0 percent (0/4)")
    monkeypatch.setattr(live, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        live, "_sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds)
    )

    record = live.poll_ping(
        live.load_devices(),
        "10.20.10.11",
        expect_success=True,
        case_id="T",
        log=FakeLog(),
        step="recheck",
        timeout=12,
    )

    assert record["ok"] is False
    assert record["success_rate"] == 0
    assert record["elapsed_s"] == 12
    assert record["attempt_count"] == 4


def test_poll_ping_tolerates_transport_error_while_polling(monkeypatch):
    def flaky(*args, **kwargs):
        flaky.calls += 1
        if flaky.calls == 1:
            raise RuntimeError("SSH 暂时不可用")
        return "Success rate is 100 percent (4/4)"

    flaky.calls = 0
    monkeypatch.setattr(live, "send_show", flaky)
    monkeypatch.setattr(live, "_sleep", lambda seconds: None)

    record = live.poll_ping(
        live.load_devices(),
        "10.20.10.11",
        expect_success=True,
        case_id="T",
        log=FakeLog(),
        step="recheck",
    )

    assert record["ok"] is True
    assert record["attempt_count"] == 2
    assert record["attempts"][0]["error"].startswith("RuntimeError")


def test_run_checks_polls_show_until_converged(monkeypatch):
    outputs = iter(
        [
            "Interface              IP-Address      OK? Method Status                Protocol\n"
            "Ethernet0/0            unassigned      YES unset  down                  down\n",
            "Interface              IP-Address      OK? Method Status                Protocol\n"
            "Ethernet0/0            unassigned      YES unset  up                    up\n",
        ]
    )

    class FakeConn:
        def __init__(self, output):
            self.output = output

        def send_command(self, command, read_timeout=30):
            return self.output

        def disconnect(self):
            pass

    monkeypatch.setattr(live, "connect", lambda *args, **kwargs: FakeConn(next(outputs)))
    monkeypatch.setattr(live, "_sleep", lambda seconds: None)

    checks = [
        {
            "device": "BR-A-SW2",
            "command": "show ip interface brief",
            "type": "line",
            "prefix": "Ethernet0/0",
            "contains": ["up"],
            "not_contains": ["administratively down"],
        }
    ]
    results = live.run_checks(
        checks,
        live.load_devices(),
        case_id="T",
        log=FakeLog(),
        step="verify-recovery",
        poll_timeout=live.SHOW_RECHECK_POLL_TIMEOUT_SECONDS,
    )

    assert results[0]["ok"] is True
    assert results[0]["round"] == 2


def test_run_checks_without_poll_returns_first_round(monkeypatch):
    class FakeConn:
        def send_command(self, command, read_timeout=30):
            return (
                "Ethernet0/0            unassigned      YES unset  "
                "administratively down down\n"
            )

        def disconnect(self):
            pass

    monkeypatch.setattr(live, "connect", lambda *args, **kwargs: FakeConn())

    checks = [
        {
            "device": "BR-A-SW2",
            "command": "show ip interface brief",
            "type": "line",
            "prefix": "Ethernet0/0",
            "contains": ["administratively down"],
        }
    ]
    results = live.run_checks(
        checks, live.load_devices(), case_id="T", log=FakeLog(), step="verify-fault"
    )

    assert results[0]["ok"] is True
    assert results[0]["round"] == 1


def test_config_diff_ignores_volatile_lines():
    before = (
        "Building configuration...\n"
        "Current configuration : 2140 bytes\n"
        "!\n"
        "! Last configuration change at 08:00:01 UTC Thu Nov 13 2026\n"
        "hostname BR-A-SW2\n"
        "interface Ethernet0/0\n"
        " switchport mode trunk\nend"
    )
    after = before.replace("08:00:01", "08:05:42")
    diff = live._diff_config_lines(before, after)
    assert diff["matches"] is True


def test_config_diff_flags_residual_config_and_redacts_secrets():
    before = (
        "hostname BR-A-SW2\n"
        "enable secret 5 $1$OLD$hash\n"
        "interface Ethernet0/0\n"
        " switchport mode trunk"
    )
    after = (
        "hostname BR-A-SW2\n"
        "enable secret 5 $1$NEW$hash\n"
        "interface Ethernet0/0\n"
        " switchport mode trunk\n"
        " switchport trunk allowed vlan 1-9,11-4094"
    )
    diff = live._diff_config_lines(before, after)
    assert diff["matches"] is False
    assert diff["removed_count"] == 1
    assert diff["added_count"] == 2
    assert diff["redacted_count"] == 2
    preview = diff["removed_preview"] + diff["added_preview"]
    assert all("secret" not in line.lower() for line in preview)
    assert " switchport trunk allowed vlan 1-9,11-4094" in diff["added_preview"]


class ScriptedInput:
    """按脚本返回输入；元素可以是字符串或要抛出的异常实例。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, prompt=""):
        self.calls += 1
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        value = self.answers.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _demo(answers, output=None):
    return live.DemoSession(
        log=FakeLog(),
        input_func=ScriptedInput(answers),
        output=output or (lambda *_: None),
    )


def _fake_execute(executed, *, status="success", demo=None):
    def execute(case, index, total):
        pause = (
            demo.wait_for_operator(case, index=index, total=total)
            if demo is not None
            else {"interrupted": False, "pause_seconds": 0.0}
        )
        executed.append({"case_id": case["id"], "index": index, "total": total})
        return {
            "case_id": case["id"],
            "status": "demo_interrupted" if pause["interrupted"] else status,
            "config_restored": True,
            "recheck_ping": {"success_rate": 100},
            "demo": {
                "mode": True,
                "interrupted": pause["interrupted"],
                "pause_seconds": pause["pause_seconds"],
            },
        }

    return execute


def test_demo_cases_exclude_control_cases():
    ids = [case["id"] for case in live.demo_cases()]
    assert len(ids) == 8
    assert "NORMAL-1" not in ids
    assert "INSUFFICIENT-1" not in ids
    with pytest.raises(live.ConfigurationError):
        live.select_demo_cases("NORMAL-1")
    with pytest.raises(live.ConfigurationError):
        live.select_demo_cases("INSUFFICIENT-1")
    assert [case["id"] for case in live.select_demo_cases("VLAN-2")] == ["VLAN-2"]


def test_demo_match_case_token():
    assert live.match_demo_case("vlan2") == "VLAN-2"
    assert live.match_demo_case("ACL_1") == "ACL-1"
    assert live.match_demo_case("route-2") == "ROUTE-2"
    assert live.match_demo_case("NORMAL-1") is None
    assert live.match_demo_case("nope") is None


def test_demo_sequence_advances_in_order():
    demo = _demo(["", "", "", "", "", ""])
    cases = [live._find_case(case_id) for case_id in ("LINK-1", "LINK-2", "VLAN-1")]
    executed: list[dict] = []

    records = live.run_demo_sequence(
        cases, demo=demo, execute=_fake_execute(executed, demo=demo), log=FakeLog()
    )

    assert [item["case_id"] for item in executed] == ["LINK-1", "LINK-2", "VLAN-1"]
    assert [item["index"] for item in executed] == [1, 2, 3]
    assert all(item["total"] == 3 for item in executed)
    assert [record["case_id"] for record in records] == ["LINK-1", "LINK-2", "VLAN-1"]


def test_demo_sequence_supports_jump():
    demo = _demo(["", "VLAN-1", "", "q"])
    cases = [live._find_case(case_id) for case_id in ("LINK-1", "LINK-2", "VLAN-1")]
    executed: list[dict] = []

    live.run_demo_sequence(
        cases, demo=demo, execute=_fake_execute(executed, demo=demo), log=FakeLog()
    )

    assert [item["case_id"] for item in executed] == ["LINK-1", "VLAN-1"]


def test_demo_sequence_quit_after_current_case():
    demo = _demo(["", "q"])
    cases = [live._find_case(case_id) for case_id in ("LINK-1", "LINK-2")]
    executed: list[dict] = []

    live.run_demo_sequence(
        cases, demo=demo, execute=_fake_execute(executed, demo=demo), log=FakeLog()
    )

    assert [item["case_id"] for item in executed] == ["LINK-1"]
    assert demo.interrupted is False


def test_demo_sequence_stops_on_failed_recovery():
    demo = _demo(["", ""])
    cases = [live._find_case(case_id) for case_id in ("LINK-1", "LINK-2")]
    executed: list[dict] = []

    live.run_demo_sequence(
        cases,
        demo=demo,
        execute=_fake_execute(executed, status="failed_recovery", demo=demo),
        log=FakeLog(),
    )

    assert [item["case_id"] for item in executed] == ["LINK-1"]


def test_demo_banner_uses_app_host_port_env(monkeypatch):
    monkeypatch.setenv("APP_HOST", "192.0.2.10")
    monkeypatch.setenv("APP_PORT", "9090")
    lines: list[str] = []
    demo = _demo([""], output=lines.append)

    result = demo.wait_for_operator(live._find_case("ACL-1"), index=7, total=8)

    text = "\n".join(lines)
    assert result == {"ok": True, "interrupted": False, "pause_seconds": demo.pause_seconds_total}
    assert "[演示 7/8] ACL-1" in text
    assert "http://192.0.2.10:9090/console/overview" in text
    assert "http://192.0.2.10:9090/console/assets" in text
    assert "http://192.0.2.10:9090/console/diagnosis" in text
    assert "http://192.0.2.10:9090/api/v1/collection-runs" in text
    assert "网络当前处于故障态" in text


def test_demo_banner_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("APP_HOST", raising=False)
    monkeypatch.delenv("APP_PORT", raising=False)
    demo = _demo([])
    assert demo.app_base_url() == "http://127.0.0.1:8000"


def test_demo_banner_ctrl_c_marks_interrupted():
    demo = _demo([KeyboardInterrupt()])
    result = demo.wait_for_operator(live._find_case("LINK-1"), index=1, total=8)
    assert result["interrupted"] is True
    assert result["ok"] is False
    assert demo.interrupted is True


def test_demo_menu_ctrl_c_quits():
    demo = _demo([KeyboardInterrupt()])
    assert demo.ask_next(live._find_case("LINK-1"), index=1, total=8) == ("quit", None)


def test_demo_menu_reprompts_on_unknown_then_jumps():
    demo = _demo(["NOPE", "vlan2"])
    assert demo.ask_next(
        live._find_case("LINK-1"), index=1, total=8, next_case_id="LINK-2"
    ) == ("jump", "VLAN-2")


def test_demo_plan_lists_pause_points(capsys):
    demo = _demo([])
    live.print_demo_plan(live.select_demo_cases("VLAN-2"), demo)
    out = capsys.readouterr().out
    assert "demo 模式计划" in out
    assert "demo 暂停点: 第 1/1" in out
    assert "NORMAL-1 / INSUFFICIENT-1 不参与 demo" in out


def test_demo_ctrl_c_still_repairs_current_case(monkeypatch, tmp_path):
    state = {"injected": False}
    apply_calls: list[str] = []

    class FakeConn:
        def send_command(self, command, read_timeout=30):
            status = "administratively down down" if state["injected"] else "up up"
            if command == "show ip interface brief":
                return (
                    "Interface              IP-Address      OK? Method Status                Protocol\n"
                    f"Ethernet0/0            unassigned      YES unset  {status}\n"
                )
            if command == "show interfaces trunk":
                return "" if state["injected"] else "Port  Mode  Encapsulation  Status\nEt0/0  on  802.1q  trunking\n"
            return ""

        def disconnect(self):
            pass

    def fake_send_show(device, command, **kwargs):
        if command == "show running-config":
            return "hostname BR-A-SW2\ninterface Ethernet0/0\n switchport mode trunk\nend\n"
        return ""

    def fake_apply(steps, devices, *, case_id, log, step_label):
        apply_calls.append(step_label)
        state["injected"] = step_label == "inject"
        return [
            {"device": step["device"], "commands": list(step["commands"])}
            for step in steps
        ]

    def fake_poll_ping(devices, target, *, expect_success, case_id, log, step, **_kwargs):
        rate = 100 if expect_success else 0
        return {
            "ok": True,
            "success_rate": rate,
            "attempts": [{"rate": rate}],
            "attempt_count": 1,
            "elapsed_s": 0,
        }

    monkeypatch.setattr(live, "connect", lambda *args, **kwargs: FakeConn())
    monkeypatch.setattr(live, "send_show", fake_send_show)
    monkeypatch.setattr(live, "apply_config_steps", fake_apply)
    monkeypatch.setattr(live, "poll_ping", fake_poll_ping)
    monkeypatch.setattr(live, "BACKUP_ROOT", tmp_path / "backups")

    demo = _demo([KeyboardInterrupt()])
    record = live.execute_case(
        live._find_case("LINK-1"),
        live.load_devices(),
        log=FakeLog(),
        run_ts="pytest",
        demo=demo,
        demo_index=1,
        demo_total=8,
    )

    assert record["injection"]["performed"] is True
    assert "inject" in apply_calls and "repair" in apply_calls
    assert record["repair"]["performed"] is True
    assert record["repair"]["ok"] is True
    assert record["demo"]["interrupted"] is True
    assert record["status"] == "demo_interrupted"
    assert record["collection"] is None
    assert record["diagnosis"] is None
    assert record["config_restored"] is True
    assert record["recheck_ping"]["success_rate"] == 100
    assert record["fault_window_s"] is not None
    assert record["fault_duration_s"] is not None


def test_write_summary_marks_demo_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "REPORTS_DIR", tmp_path)
    records = [
        {
            "case_id": "LINK-1",
            "status": "success",
            "diagnosis": None,
            "config_restored": True,
            "demo": {"mode": True, "interrupted": False, "pause_seconds": 12.5},
            "fault_window_s": 40.0,
            "fault_duration_s": 27.5,
        },
        {
            "case_id": "LINK-2",
            "status": "demo_interrupted",
            "diagnosis": None,
            "demo": {"mode": True, "interrupted": True, "pause_seconds": 3.0},
        },
    ]

    summary = live.write_summary(records, log=FakeLog(), run_ts="pytest", mode="demo")

    assert summary["mode"] == "demo"
    assert summary["case_count"] == 2
    assert summary["demo"]["cases"] == ["LINK-1", "LINK-2"]
    assert summary["demo"]["pause_seconds_total"] == 15.5
    assert summary["demo"]["interrupted"] is True
    assert records[0]["fault_duration_s"] == 27.5
    assert (tmp_path / "fault_experiments_summary_pytest.json").exists()


def test_write_summary_defaults_to_auto_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "REPORTS_DIR", tmp_path)
    summary = live.write_summary([], log=FakeLog(), run_ts="pytest-auto")
    assert summary["mode"] == "auto"
    assert summary["demo"] is None


def test_demo_sequence_hard_interrupt_stops_and_flags():
    demo = _demo([])
    cases = [live._find_case(case_id) for case_id in ("LINK-1", "LINK-2")]
    executed: list[dict] = []

    def execute(case, index, total):
        executed.append(case["id"])
        raise KeyboardInterrupt

    records = live.run_demo_sequence(
        cases, demo=demo, execute=execute, log=FakeLog()
    )

    assert executed == ["LINK-1"]
    assert records == []
    assert demo.hard_interrupted is True
