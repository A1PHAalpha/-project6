#!/usr/bin/env python3
"""EVE-NG 真实故障注入 + 有/无知识库诊断对比（固化脚本）。

8 步模板与拓扑事实以 ``docs/TEST_RUNBOOK.md`` §1 与 §10 为准：

    a 备份运行配置 -> b 注入 -> c 验证故障现象 -> d 触发采集
    -> e 双诊断 -> f 逆操作修复 -> g 从 HQ-R1 复测 ping -> h 确认恢复

    约定：

    * 只改运行配置，绝不执行 ``write memory`` / ``copy run start``；注入了逆操作
      命令的案例，逆操作一定放在 ``finally`` 里执行。
    * 恢复期用轮询而不是固定次数探测：IOSvL2 在 ``no shutdown`` 之后要等生成树
      把端口转入 forwarding（实测约 30 秒），``show interfaces trunk`` 也要等 STP
      收敛才重新出现 trunk 端口。轮询参数见文件顶部常量。
    * 注入只针对业务接口，脚本会拒绝任何以 ``Ethernet3/0`` / ``Vlan999``
      管理面为目标或在命令里出现持久化关键字的操作。
    * 凭据只从 ``.env`` / 环境变量读取，日志只记录命令与断言结论，不记录口令，
      也不把 ``show running-config`` 全文写进日志。

用法：

    python scripts/live_fault_experiments.py --list
    python scripts/live_fault_experiments.py --case LINK-1 --dry-run
    python scripts/live_fault_experiments.py --all
    python scripts/live_fault_experiments.py --collect-baseline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import env_flag, load_project_env  # noqa: E402

load_project_env()

from test_cases.evaluation import LIVE_EXPECTED_BY_ID, expected_for_live_case  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "reports"
BACKUP_ROOT = REPORTS_DIR / "backups"
BASELINE_DIR = REPORTS_DIR / "baselines"
INVENTORY_PATH = PROJECT_ROOT / "network" / "device_inventory.json"
COLLECTION_TIMEOUT_SECONDS = 900
DEFAULT_SSH_TIMEOUT = 20

# 恢复期必须轮询而不是固定次数探测：IOSvL2 在 no shutdown 之后要等生成树把端口
# 转入 forwarding（实测约 30 秒），trunk 端口也要等 STP 收敛才会重新出现在
# show interfaces trunk 里。固定 2 次探测会把健康的恢复误判成 failed_recovery。
PING_POLL_INTERVAL_SECONDS = 5
PRECHECK_POLL_TIMEOUT_SECONDS = 30
FAULT_POLL_TIMEOUT_SECONDS = 30
RECHECK_POLL_TIMEOUT_SECONDS = 75
SHOW_POLL_INTERVAL_SECONDS = 5
SHOW_RECHECK_POLL_TIMEOUT_SECONDS = 45

DEMO_APP_HOST_DEFAULT = "127.0.0.1"
DEMO_APP_PORT_DEFAULT = "8000"
DEMO_CASE_COUNT = 8

PING_SOURCE = "10.10.10.1"
RE_CHECK_PING_DEVICE = "HQ-R1"

FORBIDDEN_CONFIG_MARKERS = (
    "write memory",
    "wr mem",
    "write startup-config",
    "copy run",
    "copy running-config",
    "reload",
)
MGMT_INTERFACE_MARKERS = ("ethernet3/0", "vlan999")

_PING_RATE_RE = re.compile(r"Success rate is (\d+) percent \((\d+)/(\d+)\)")
_CONFIG_ERROR_MARKERS = (
    "% invalid input",
    "% incomplete command",
    "% ambiguous command",
    "% unrecognized command",
    "% authorization failed",
)
_SECRET_LINE_MARKERS = ("secret", "password", "community", "key ", "snmp-server host")


class ConfigurationError(RuntimeError):
    """环境变量或设备台账不完整，无法继续。"""


class UnsafeCommandError(RuntimeError):
    """注入/修复命令触碰了持久化或管理面接口。"""


class ScenarioAbort(RuntimeError):
    """案例断言未通过，后续步骤不再执行但必须继续做恢复。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _short(text: str, limit: int = 220) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    return normalized[:limit] + ("..." if len(normalized) > limit else "")


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


class RunLog:
    """时间戳日志：同时写标准输出与 reports/*.log。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = path.open("a", encoding="utf-8")

    def event(self, level: str, case_id: str | None, step: str, message: str) -> None:
        line = f"{_now_iso()} [{level:5}] [{case_id or '-':4}] [{step:>10}] {message}"
        print(line, flush=True)
        self._handle.write(line + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class DemoSession:
    """人工演示模式的交互层：只依赖 ``input()``，构造时可注入函数便于单测。"""

    def __init__(self, *, log: "RunLog", input_func: Callable[[str], str] | None = None,
                 output: Callable[[str], None] | None = None):
        self.log = log
        self._input = input_func or input
        self._output = output or print
        self.interrupted = False
        self.hard_interrupted = False
        self.pause_seconds_total = 0.0
        self.completed_ids: set[str] = set()
        self.events: list[dict] = []

    def app_base_url(self) -> str:
        host = (os.getenv("APP_HOST") or DEMO_APP_HOST_DEFAULT).strip()
        port = (os.getenv("APP_PORT") or DEMO_APP_PORT_DEFAULT).strip()
        return f"http://{host}:{port}"

    def endpoints(self) -> dict[str, str]:
        base = self.app_base_url()
        return {
            "overview": f"{base}/console/overview",
            "assets": f"{base}/console/assets",
            "diagnosis": f"{base}/console/diagnosis",
            "collection_api": f"{base}/api/v1/collection-runs",
        }

    def emit(self, lines: list[str]) -> None:
        for line in lines:
            self._output(line)

    def announce_start(self, total: int) -> None:
        urls = self.endpoints()
        self.emit(
            [
                "=" * 68,
                f"人工演示模式（demo）：{total} 个注入案例顺序执行",
                f"Web 控制台：{urls['overview']}",
                "每个案例注入并验证故障后会暂停，等你完成 webapp 操作；按 Enter 自动恢复。",
                "Ctrl+C = 立即恢复当前案例并退出；案例间可输入 q 退出。",
                "=" * 68,
            ]
        )
        self.log.event("INFO", None, "demo-start", f"demo 模式启动，共 {total} 个案例，入口 {urls['overview']}")

    def wait_for_operator(self, case: dict, *, index: int, total: int) -> dict:
        urls = self.endpoints()
        self.emit(
            [
                "",
                "=" * 68,
                f"[演示 {index}/{total}] {case['id']}  {case['category']}",
                f"故障：{case.get('description') or case['id']}",
                f"受影响设备：{case.get('device') or '-'}",
                "-" * 68,
                "网络当前处于故障态（脚本不会再改动设备）。可在 Web 控制台完成：",
                f"  1. 控制台总览   {urls['overview']}",
                f"  2. 设备资产     {urls['assets']}",
                f"  3. 诊断页       {urls['diagnosis']}",
                "     发起自然语言诊断，查看证据、检索依据与修复建议",
                f"  4. 手动采集     POST {urls['collection_api']}",
                "     建议先触发一次采集，再在诊断页提问，观察证据与结论是否吻合",
                "-" * 68,
                "提示：故障期间按 Enter 立即恢复该案例；Ctrl+C 也会先恢复再退出。",
                "=" * 68,
            ]
        )
        self.log.event(
            "INFO",
            case["id"],
            "demo-banner",
            f"演示 {index}/{total} 暂停等待操作员确认（{urls['overview']}）",
        )
        started = _monotonic()
        try:
            self._input("按 Enter 恢复该案例: ")
            pause_seconds = round(_monotonic() - started, 1)
            self.pause_seconds_total += pause_seconds
            self.emit([f"收到确认，开始自动恢复（暂停 {pause_seconds}s）。", ""])
            self.log.event(
                "INFO", case["id"], "demo-pause", f"操作员确认，暂停 {pause_seconds}s，开始逆操作"
            )
            result = {"ok": True, "interrupted": False, "pause_seconds": pause_seconds}
        except (KeyboardInterrupt, EOFError):
            pause_seconds = round(_monotonic() - started, 1)
            self.pause_seconds_total += pause_seconds
            self.interrupted = True
            self.emit(["", "检测到中断（Ctrl+C）：立即恢复当前案例，恢复完成后退出演示。", ""])
            self.log.event(
                "WARN", case["id"], "demo-pause", f"操作员中断，转入恢复流程（暂停 {pause_seconds}s）"
            )
            result = {"ok": False, "interrupted": True, "pause_seconds": pause_seconds}
        self.events.append({"case_id": case["id"], "step": "pause", **result})
        return result

    def report_recovered(
        self, case: dict, record: dict, *, index: int, total: int
    ) -> None:
        ping = record.get("recheck_ping") or {}
        rate = ping.get("success_rate")
        detail = f"复测 ping {rate}%" if rate is not None else "复测已完成"
        restored = record.get("config_restored")
        if restored is True:
            detail += "，running-config 与注入前备份一致"
        elif restored is False:
            detail += "，running-config 与备份仍有差异（见报告）"
        self.emit(
            [
                "",
                "=" * 68,
                f"[演示 {index}/{total}] {case['id']} 已恢复：{detail}",
                "-" * 68,
            ]
        )
        self.log.event("INFO", case["id"], "demo-recovered", f"已恢复：{detail}")

    def ask_next(
        self, case: dict, *, index: int, total: int, next_case_id: str | None = None
    ) -> tuple[str, str | None]:
        hint = (
            f"Enter = 下一案例（{next_case_id}）"
            if next_case_id
            else "Enter = 结束演示"
        )
        self.emit(
            [
                f"{hint}｜输入案例 ID = 跳转（LINK-1/2、VLAN-1/2、ROUTE-1/2、ACL-1/2）"
                "｜q = 退出演示（剩余案例跳过，网络已恢复）",
            ]
        )
        while True:
            try:
                raw = self._input("演示> ")
            except (KeyboardInterrupt, EOFError):
                self.emit(["", "已退出演示（当前案例已恢复，无遗留故障）。", ""])
                self.log.event("WARN", case["id"], "demo-menu", "操作员中断，退出演示")
                return ("quit", None)
            answer = (raw or "").strip()
            if not answer:
                return ("next", None)
            if answer.lower() in ("q", "quit", "exit"):
                return ("quit", None)
            target = match_demo_case(answer)
            if target:
                return ("jump", target)
            self.emit([f"未识别的输入 {answer!r}，请输入案例 ID（如 VLAN-2）或 q。"])


def load_devices() -> dict[str, dict]:
    payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    devices = {}
    for item in payload.get("devices", []):
        devices[item["device_name"]] = item
    return devices


def require_device(devices: dict[str, dict], name: str) -> dict:
    device = devices.get(name)
    if not device:
        raise ConfigurationError(f"设备台账中不存在 {name}")
    return device


def require_credentials() -> tuple[str, str]:
    username = os.getenv("DEVICE_USERNAME")
    password = os.getenv("DEVICE_PASSWORD")
    if not username or not password:
        raise ConfigurationError("未配置 DEVICE_USERNAME / DEVICE_PASSWORD，无法连接设备")
    return username, password


def guard_commands(commands: list[str], *, purpose: str) -> None:
    for command in commands:
        lowered = command.strip().lower()
        for marker in FORBIDDEN_CONFIG_MARKERS:
            if marker in lowered:
                raise UnsafeCommandError(
                    f"{purpose} 命令包含持久化关键字 '{marker}'，脚本拒绝执行: {command}"
                )
        for marker in MGMT_INTERFACE_MARKERS:
            if marker in lowered:
                raise UnsafeCommandError(
                    f"{purpose} 命令触碰管理面接口 '{marker}'，脚本拒绝执行: {command}"
                )


def connect(device: dict, *, case_id: str, log: RunLog):
    from netmiko import ConnectHandler

    username, password = require_credentials()
    params = {
        "device_type": device.get("device_type") or "cisco_ios",
        "host": device["mgmt_ip"],
        "username": username,
        "password": password,
        "port": int(device.get("ssh_port") or 22),
        "timeout": DEFAULT_SSH_TIMEOUT,
    }
    if env_flag("SSH_LEGACY_ALGOS", default=False):
        params["disabled_algorithms"] = {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            return ConnectHandler(**params)
        except Exception as exc:  # noqa: BLE001 - 连接失败原因需要原样上报
            last_error = exc
            if attempt == 2:
                break
            log.event(
                "WARN",
                case_id,
                "ssh",
                f"{device['device_name']} 连接失败（第 1 次），2 秒后重试: {type(exc).__name__}",
            )
            time.sleep(2)
    raise RuntimeError(
        f"SSH 连接 {device['device_name']}({device['mgmt_ip']}) 失败: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def send_show(device: dict, command: str, *, case_id: str, log: RunLog, read_timeout: int = 30) -> str:
    conn = connect(device, case_id=case_id, log=log)
    try:
        return conn.send_command(command, read_timeout=read_timeout)
    finally:
        conn.disconnect()


def apply_config_steps(
    steps: list[dict],
    devices: dict[str, dict],
    *,
    case_id: str,
    log: RunLog,
    step_label: str,
) -> list[dict]:
    applied = []
    for step in steps:
        device = require_device(devices, step["device"])
        guard_commands(step["commands"], purpose=f"{case_id}/{step_label}")
        log.event("INFO", case_id, step_label, f"{device['device_name']} 下发: {' | '.join(step['commands'])}")
        conn = connect(device, case_id=case_id, log=log)
        try:
            output = conn.send_config_set(step["commands"], read_timeout=60)
        finally:
            conn.disconnect()
        lowered = (output or "").lower()
        errors = [marker for marker in _CONFIG_ERROR_MARKERS if marker in lowered]
        if errors:
            message = (
                f"{device['device_name']} 配置命令被设备拒绝({errors}): "
                f"{_short(output, 200)}"
            )
            if step.get("optional"):
                log.event("WARN", case_id, step_label, f"可选步骤未生效（继续）: {message}")
            else:
                raise RuntimeError(message)
        applied.append(
            {
                "device": device["device_name"],
                "commands": list(step["commands"]),
                "optional": bool(step.get("optional")),
                "errors": errors,
                "output_sha256": _sha256(output or ""),
                "output_excerpt": _short(output, 160),
            }
        )
    return applied


def _port_aliases(port: str) -> set[str]:
    name = (port or "").strip().lower()
    aliases = {name}
    for full, short in (
        ("ethernet", "et"),
        ("gigabitethernet", "gi"),
        ("fastethernet", "fa"),
    ):
        if name.startswith(full):
            aliases.add(short + name[len(full):])
        elif name.startswith(short):
            aliases.add(full + name[len(short):])
    return aliases


def _vlan_in_list(vlan_list: str, vlan: int) -> bool:
    for part in (vlan_list or "").split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            low, _, high = chunk.partition("-")
            try:
                if int(low) <= vlan <= int(high):
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(chunk) == vlan:
                    return True
            except ValueError:
                continue
    return False


def _parse_ping_rate(output: str) -> int | None:
    match = _PING_RATE_RE.search(output or "")
    if match:
        return int(match.group(1))
    return None


def _matches_line(output: str, spec: dict) -> tuple[bool, str]:
    prefix = spec["prefix"].lower()
    target = None
    for raw in (output or "").splitlines():
        if raw.strip().lower().startswith(prefix):
            target = re.sub(r"[ \t]+", " ", raw.strip())
            break
    if target is None:
        return False, f"未找到以 {spec['prefix']} 开头的行"
    lowered = target.lower()
    missing = [item for item in spec.get("contains", []) if item.lower() not in lowered]
    unexpected = [item for item in spec.get("not_contains", []) if item.lower() in lowered]
    ok = not missing and not unexpected
    detail = f"行={target}"
    if missing:
        detail += f" 缺少={missing}"
    if unexpected:
        detail += f" 不应出现={unexpected}"
    return ok, detail


def _matches_text(output: str, spec: dict) -> tuple[bool, str]:
    lowered = re.sub(r"[ \t]+", " ", output or "").lower()
    missing = [item for item in spec.get("contains", []) if item.lower() not in lowered]
    present = [item for item in spec.get("not_contains", []) if item.lower() in lowered]
    ok = not missing and not present
    detail = ""
    if missing:
        detail += f"缺少={missing} "
    if present:
        detail += f"不应出现={present}"
    return ok, detail.strip() or "文本断言通过"


def _vlan_ports(output: str, spec: dict) -> tuple[bool, str]:
    vlan_id = str(spec["vlan_id"])
    aliases = _port_aliases(spec["port"])
    line = None
    member = False
    for raw in (output or "").splitlines():
        parts = raw.split()
        if not parts or parts[0] != vlan_id:
            continue
        line = re.sub(r"[ \t]+", " ", raw.strip())
        tokens = [token.strip().lower() for token in re.split(r"[\s,]+", raw) if token.strip()]
        member = any(token in aliases for token in tokens)
        break
    if line is None:
        return False, f"show vlan brief 中未找到 VLAN {vlan_id}"
    ok = member == bool(spec["expect_member"])
    return ok, f"VLAN {vlan_id} {'包含' if member else '不包含'} {spec['port']}；行={line}"


def _trunk_vlans(output: str, spec: dict) -> tuple[bool, str]:
    aliases = _port_aliases(spec["port"])
    vlan = int(spec["vlan_id"])
    in_section = False
    allowed = None
    for raw in (output or "").splitlines():
        stripped = raw.strip()
        lowered = stripped.lower()
        if "vlans allowed on trunk" in lowered:
            in_section = True
            continue
        if not in_section or not stripped:
            continue
        if (
            lowered.startswith("port")
            or lowered.startswith("vlans allowed and active")
            or lowered.startswith("vlans in spanning tree")
        ):
            break
        tokens = stripped.split()
        if len(tokens) < 2:
            continue
        if tokens[0].lower() in aliases:
            allowed = _vlan_in_list(tokens[1], vlan)
            break
    if allowed is None:
        return False, f"show interfaces trunk 中未找到 {spec['port']} 的 allowed 列表"
    ok = allowed == bool(spec["expect_allowed"])
    return ok, f"{spec['port']} allowed VLAN {vlan} = {allowed}"


def evaluate_check(spec: dict, output: str) -> tuple[bool, str]:
    kind = spec["type"]
    if kind == "text":
        return _matches_text(output, spec)
    if kind == "line":
        return _matches_line(output, spec)
    if kind == "vlan_ports":
        return _vlan_ports(output, spec)
    if kind == "trunk_vlans":
        return _trunk_vlans(output, spec)
    if kind == "ping":
        rate = _parse_ping_rate(output)
        if rate is None:
            return False, f"无法解析 ping 成功率: {_short(output, 120)}"
        expect_success = spec.get("expect") == "success"
        ok = (rate == 100) if expect_success else (rate == 0)
        return ok, f"成功率={rate}% 期望={'100%' if expect_success else '0%'}"
    raise ValueError(f"未知断言类型: {kind}")


def _run_checks_once(
    checks: list[dict],
    devices: dict[str, dict],
    *,
    case_id: str,
    log: RunLog,
    step: str,
    round_no: int,
) -> list[dict]:
    results: list[dict] = []
    by_device: dict[str, list[dict]] = {}
    for spec in checks:
        by_device.setdefault(spec["device"], []).append(spec)

    for device_name, specs in by_device.items():
        device = require_device(devices, device_name)
        conn = connect(device, case_id=case_id, log=log)
        try:
            cached: dict[str, str] = {}
            for spec in specs:
                command = spec["command"]
                if command not in cached:
                    cached[command] = conn.send_command(command, read_timeout=30)
                ok, detail = evaluate_check(spec, cached[command])
                results.append(
                    {
                        "device": device_name,
                        "command": command,
                        "type": spec["type"],
                        "ok": ok,
                        "round": round_no,
                        "detail": _short(detail),
                    }
                )
                log.event(
                    "INFO" if ok else "WARN",
                    case_id,
                    step,
                    f"第{round_no}轮 {device_name} :: {command} -> "
                    f"{'OK' if ok else 'FAIL'} ({_short(detail, 160)})",
                )
        finally:
            conn.disconnect()
    return results


def run_checks(
    checks: list[dict],
    devices: dict[str, dict],
    *,
    case_id: str,
    log: RunLog,
    step: str,
    poll_timeout: float = 0,
    poll_interval: float = SHOW_POLL_INTERVAL_SECONDS,
) -> list[dict]:
    """执行 show 断言；``poll_timeout>0`` 时在窗口内轮询，最后返回末轮结果。"""
    started = _monotonic()
    round_no = 0
    while True:
        round_no += 1
        results = _run_checks_once(
            checks, devices, case_id=case_id, log=log, step=step, round_no=round_no
        )
        failed = [item for item in results if not item["ok"]]
        if not failed:
            if round_no > 1:
                log.event(
                    "INFO",
                    case_id,
                    step,
                    f"第{round_no}轮全部断言通过（累计等待 {round(_monotonic() - started, 1)}s）",
                )
            return results
        elapsed = _monotonic() - started
        if elapsed >= poll_timeout:
            if poll_timeout > 0:
                log.event(
                    "ERROR",
                    case_id,
                    step,
                    f"轮询 {round(elapsed, 1)}s 后仍有 {len(failed)} 条断言未通过",
                )
            else:
                log.event(
                    "ERROR",
                    case_id,
                    step,
                    f"断言未通过（未启用轮询）: {len(failed)} 条",
                )
            return results
        wait = min(poll_interval, max(0.0, poll_timeout - elapsed))
        log.event(
            "WARN",
            case_id,
            step,
            f"第{round_no}轮有 {len(failed)} 条断言未通过，{round(wait, 1)}s 后重试",
        )
        _sleep(wait)


def poll_ping(
    devices: dict[str, dict],
    target: str,
    *,
    expect_success: bool,
    case_id: str,
    log: RunLog,
    step: str,
    interval: float = PING_POLL_INTERVAL_SECONDS,
    timeout: float = RECHECK_POLL_TIMEOUT_SECONDS,
) -> dict:
    """按固定间隔轮询 ping，命中期望成功率即通过，超过 ``timeout`` 才判失败。"""
    source_device = require_device(devices, RE_CHECK_PING_DEVICE)
    command = f"ping {target} source {PING_SOURCE} repeat 4"
    expected = 100 if expect_success else 0
    started = _monotonic()
    record = {
        "device": RE_CHECK_PING_DEVICE,
        "command": command,
        "expect_success": expect_success,
        "expect_success_rate": expected,
        "interval_s": interval,
        "timeout_s": timeout,
        "attempts": [],
    }
    attempt = 0
    while True:
        attempt += 1
        output = ""
        rate: int | None = None
        error: str | None = None
        try:
            output = send_show(source_device, command, case_id=case_id, log=log, read_timeout=40)
            rate = _parse_ping_rate(output)
        except Exception as exc:  # noqa: BLE001 - 轮询期间单次异常不应终止恢复判定
            error = f"{type(exc).__name__}: {exc}"
        elapsed = _monotonic() - started
        record["attempts"].append(
            {
                "attempt": attempt,
                "rate": rate,
                "elapsed_s": round(elapsed, 1),
                "error": error,
                "output_excerpt": _short(output, 160),
            }
        )
        if rate is not None and rate == expected:
            record.update(
                ok=True,
                success_rate=rate,
                elapsed_s=round(elapsed, 1),
                attempt_count=attempt,
            )
            log.event(
                "INFO",
                case_id,
                step,
                f"第{attempt}次 ping {target} 成功率={rate}%（期望 {expected}%，"
                f"累计等待 {round(elapsed, 1)}s）",
            )
            return record
        remaining = timeout - elapsed
        if remaining <= 0:
            break
        wait = min(interval, remaining)
        log.event(
            "WARN",
            case_id,
            step,
            f"第{attempt}次 ping {target} 成功率={rate}%（期望 {expected}%），"
            f"{round(wait, 1)}s 后继续等待",
        )
        _sleep(wait)
    record.update(
        ok=False,
        success_rate=record["attempts"][-1]["rate"],
        elapsed_s=record["attempts"][-1]["elapsed_s"],
        attempt_count=attempt,
    )
    log.event(
        "ERROR",
        case_id,
        step,
        f"轮询 {round(record['elapsed_s'], 1)}s（{attempt} 次）后 ping {target} "
        f"成功率仍为 {record['success_rate']}%（期望 {expected}%）",
    )
    return record


def backup_configs(
    case_id: str,
    device_names: list[str],
    devices: dict[str, dict],
    *,
    run_ts: str,
    log: RunLog,
) -> dict:
    backup_dir = BACKUP_ROOT / f"fault_{case_id.replace('-', '_')}_{run_ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    record = {"dir": str(backup_dir), "files": [], "ok": True, "error": None}
    for name in device_names:
        device = require_device(devices, name)
        try:
            config = send_show(device, "show running-config", case_id=case_id, log=log, read_timeout=60)
        except Exception as exc:  # noqa: BLE001
            record["ok"] = False
            record["error"] = f"{name}: {type(exc).__name__}: {exc}"
            log.event("ERROR", case_id, "backup", f"{name} 备份失败: {type(exc).__name__}")
            continue
        path = backup_dir / f"{name}.cfg"
        path.write_text(config, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        record["files"].append(
            {
                "device": name,
                "path": str(path),
                "line_count": len(config.splitlines()),
                "sha256": _sha256(config),
            }
        )
        log.event(
            "INFO",
            case_id,
            "backup",
            f"{name} 运行配置已备份 -> {path}（{len(config.splitlines())} 行）",
        )
    return record


def run_collection(
    case_id: str,
    device_names: list[str],
    *,
    log: RunLog,
    db_path: str | None = None,
) -> dict:
    command = [
        sys.executable,
        "-m",
        "collector.run_collect",
        "--devices",
        ",".join(device_names),
        "--trigger",
        "manual",
        "--requested-by",
        "live_fault_experiment",
    ]
    env = os.environ.copy()
    if db_path:
        env["SQLITE_DB_PATH"] = db_path
    log.event("INFO", case_id, "collect", f"执行: {' '.join(command)}")
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COLLECTION_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.event("ERROR", case_id, "collect", f"采集超时（>{COLLECTION_TIMEOUT_SECONDS}s）")
        return {"exit_code": None, "run_id": None, "ok": False, "error": "timeout"}

    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    match = re.search(r"'run_id':\s*'([^']+)'", output)
    run_id = match.group(1) if match else None
    ok = completed.returncode == 0 and bool(run_id)
    log.event(
        "INFO" if ok else "ERROR",
        case_id,
        "collect",
        f"退出码={completed.returncode} run_id={run_id} 耗时={round(time.time() - started, 1)}s",
    )
    if not ok:
        tail = _short(output, 400)
        log.event("ERROR", case_id, "collect", f"采集输出末尾: {tail}")
    return {
        "exit_code": completed.returncode,
        "run_id": run_id,
        "ok": ok,
        "started_at": _now_iso(),
        "duration_s": round(time.time() - started, 1),
    }


def _variant_summary(result: dict) -> dict:
    score = result.get("score") or {}
    answer = result.get("answer") or {}
    return {
        "diagnosis_id": result.get("diagnosis_id"),
        "kb_enabled": result.get("kb_enabled"),
        "correctness": score.get("correctness"),
        "correctness_score": score.get("correctness_score"),
        "executability_score": score.get("executability_score"),
        "matched_terms": score.get("matched_terms"),
        "missing_terms": score.get("missing_terms"),
        "matched_devices": score.get("matched_devices"),
        "forbidden_hits": score.get("forbidden_hits"),
        "fault_phenomenon": _short(answer.get("fault_phenomenon"), 300),
        "missing_information": _short(answer.get("missing_information"), 300),
    }


def diagnose_case(case: dict, expected: dict, *, log: RunLog) -> dict:
    from test_cases.real_evaluation import run_real_case

    log.event(
        "INFO",
        case["id"],
        "diagnose",
        f"run_real_case(external_id={case['external_id']}) 有/无知识库各一次",
    )
    payload = run_real_case(
        case_external_id=case["external_id"],
        category=case["category"],
        question=case["question"],
        expected=expected,
        expected_notes=case.get("expected_notes", ""),
        suite_name="EVE-NG 真实故障注入评测",
        created_by="live-fault-experiment",
        device_name=case.get("device"),
        fault_type=case["category"] if case.get("inject") else None,
        fault_description=case.get("description"),
    )
    results = payload.get("results") or []
    by_variant = {bool(item.get("kb_enabled")): _variant_summary(item) for item in results}
    summary = payload.get("summary") or {}
    record = {
        "experiment_id": payload.get("experiment_id"),
        "case_id": payload.get("case_id"),
        "suite_id": payload.get("suite_id"),
        "same_evidence": summary.get("same_evidence_all_cases"),
        "with_kb": by_variant.get(True, {}),
        "without_kb": by_variant.get(False, {}),
        "response_time_ms": {
            "with_kb": (summary.get("by_variant") or {}).get("with_kb", {}).get(
                "avg_total_response_time_ms"
            ),
            "without_kb": (summary.get("by_variant") or {}).get("without_kb", {}).get(
                "avg_total_response_time_ms"
            ),
        },
    }
    for key in ("with_kb", "without_kb"):
        variant = record[key]
        variant["total_response_time_ms"] = record["response_time_ms"][key]
        log.event(
            "INFO" if variant.get("correctness") == "correct" else "WARN",
            case["id"],
            "diagnose",
            f"{key}: diagnosis_id={variant.get('diagnosis_id')} "
            f"correctness={variant.get('correctness')} "
            f"score={variant.get('correctness_score')} "
            f"missing={variant.get('missing_terms')} "
            f"响应={record['response_time_ms'][key]}ms",
        )
    return record


def prepare_isolated_db(path: Path) -> str:
    """把 ``collector.db`` 指向独立库（沿用 test_cases/compare_kb 的做法）。"""
    from collector import db

    old_path = db.DB_PATH
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{path}{suffix}")
        if target.exists():
            target.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    db.DB_PATH = str(path)
    db.init_db()
    db.load_inventory(str(INVENTORY_PATH))
    return old_path


def restore_db_path(old_path: str) -> None:
    from collector import db

    db.DB_PATH = old_path


def print_case_plan(case: dict) -> None:
    print(f"\n=== {case['id']} ({case['category']})")
    print(f"  描述: {case.get('description')}")
    print(f"  受影响设备: {case.get('device') or '-'}  采集设备: {', '.join(case['collect_devices'])}")
    print(f"  诊断问题: {case['question']}")
    print(f"  a 备份: show running-config @ {', '.join(case.get('backup_devices') or [])}")
    ping = case.get("ping")
    if ping:
        print(
            f"      基线 ping: {RE_CHECK_PING_DEVICE} :: ping {ping['target']} "
            f"source {PING_SOURCE} repeat 4 期望 100%（最多等 {PRECHECK_POLL_TIMEOUT_SECONDS}s）"
        )
    if case.get("inject"):
        for step in case["inject"]:
            print(f"  b 注入 @ {step['device']}: {' | '.join(step['commands'])}")
    else:
        print("  b 注入: 无（对照案例）")
    print(f"  c 验证: {len(case.get('fault_checks') or [])} 条 show 断言")
    for spec in case.get("fault_checks") or []:
        print(f"      - {spec['device']} :: {spec['command']} [{spec['type']}]")
    if ping and case.get("inject"):
        print(
            f"      - {RE_CHECK_PING_DEVICE} :: ping {ping['target']} source {PING_SOURCE} "
            f"repeat 4 期望 0%（最多等 {FAULT_POLL_TIMEOUT_SECONDS}s）"
        )
    print(f"  d 采集: python -m collector.run_collect --devices {','.join(case['collect_devices'])}")
    print(f"  e 诊断: run_real_case({case['external_id']}) use_kb=True/False")
    expected = expected_for_live_case(case["id"])
    print(
        "      expected: must_mention={} must_mention_any={} devices={} forbidden={}".format(
            expected["must_mention"],
            expected["must_mention_any"],
            expected["expected_devices"],
            expected["forbidden"],
        )
    )
    if case.get("repair"):
        for step in case["repair"]:
            print(f"  f 修复 @ {step['device']}: {' | '.join(step['commands'])}")
        print(
            f"  g 复测: {RE_CHECK_PING_DEVICE} ping {ping['target'] if ping else '-'} "
            f"每 {PING_POLL_INTERVAL_SECONDS}s 轮询，最多等 {RECHECK_POLL_TIMEOUT_SECONDS}s"
            f"（STP 收敛需要约 30s）"
        )
        print(
            f"  h 恢复确认: {len(case.get('recovery_checks') or [])} 条 show 断言"
            f"（最多轮询 {SHOW_RECHECK_POLL_TIMEOUT_SECONDS}s）"
            " + running-config 与注入前备份逐个设备 diff"
        )
    if case.get("isolated_db"):
        print("  * 该案例使用独立 SQLite 库，仅采集 HQ 设备以制造 no_data 证据缺口")


def _normalize_config_lines(text: str) -> list[str]:
    """去掉注释/时间戳/build 提示等易变行，只比较真正的配置命令。"""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!"):
            continue
        if stripped.startswith("Building configuration"):
            continue
        if stripped.startswith("Current configuration"):
            continue
        lines.append(line)
    return lines


def _diff_config_lines(before: str, after: str, limit: int = 12) -> dict:
    before_lines = _normalize_config_lines(before)
    after_lines = _normalize_config_lines(after)
    before_set, after_set = set(before_lines), set(after_lines)
    removed = [line for line in before_lines if line not in after_set]
    added = [line for line in after_lines if line not in before_set]
    secret = [line for line in removed + added if any(m in line.lower() for m in _SECRET_LINE_MARKERS)]
    secret_set = set(secret)
    visible_removed = [line for line in removed if line not in secret_set]
    visible_added = [line for line in added if line not in secret_set]
    return {
        "matches": not removed and not added,
        "removed_count": len(removed),
        "added_count": len(added),
        "redacted_count": len(secret),
        "removed_preview": visible_removed[:limit],
        "added_preview": visible_added[:limit],
    }


def verify_config_restored(
    case: dict,
    record: dict,
    devices: dict[str, dict],
    *,
    log: RunLog,
) -> list[dict]:
    """把恢复后的 running-config 与注入前备份逐行比对（敏感行只计数不落盘）。"""
    results: list[dict] = []
    case_id = case["id"]
    backup_files = {
        item["device"]: item for item in (record.get("backup") or {}).get("files", [])
    }
    for step in case.get("inject") or []:
        name = step["device"]
        backup = backup_files.get(name)
        if not backup:
            continue
        try:
            current = send_show(
                require_device(devices, name),
                "show running-config",
                case_id=case_id,
                log=log,
                read_timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"device": name, "matches": False, "error": f"{type(exc).__name__}: {exc}"}
            )
            log.event("ERROR", case_id, "config-diff", f"{name} 恢复比对失败: {exc}")
            continue
        before = Path(backup["path"]).read_text(encoding="utf-8")
        diff = {"device": name, "backup_path": backup["path"], **_diff_config_lines(before, current)}
        results.append(diff)
        log.event(
            "INFO" if diff["matches"] else "WARN",
            case_id,
            "config-diff",
            f"{name} 与备份一致={diff['matches']} 删除={diff['removed_count']} "
            f"新增={diff['added_count']} 敏感行={diff['redacted_count']}",
        )
    return results


def execute_case(
    case: dict,
    devices: dict[str, dict],
    *,
    log: RunLog,
    run_ts: str,
    demo: DemoSession | None = None,
    demo_index: int = 1,
    demo_total: int = 1,
) -> dict:
    case_id = case["id"]
    expected = expected_for_live_case(case_id)
    record: dict = {
        "case_id": case_id,
        "external_id": case["external_id"],
        "category": case["category"],
        "description": case.get("description"),
        "device": case.get("device"),
        "collect_devices": list(case["collect_devices"]),
        "question": case["question"],
        "expected": expected,
        "status": "failed",
        "started_at": _now_iso(),
        "backup": None,
        "precheck_ping": None,
        "injection": {"performed": False, "steps": [], "error": None},
        "fault_verification": {"checks": [], "ok": False},
        "fault_ping": None,
        "collection": None,
        "diagnosis": None,
        "repair": {"performed": False, "steps": [], "ok": False, "error": None},
        "recheck_ping": None,
        "recovery_verification": {"checks": [], "ok": False},
        "isolated_db": None,
        "notes": case.get("notes"),
        "demo": None,
        "fault_window_s": None,
        "fault_duration_s": None,
        "failure_reasons": [],
        "error": None,
    }
    log.event("INFO", case_id, "start", case.get("description") or case_id)

    old_db_path: str | None = None
    injected = False
    fault_verified = False
    diagnosis_ok = False
    injection_finished_at: float | None = None
    repair_started_at: float | None = None

    try:
        if case.get("isolated_db"):
            scratch = PROJECT_ROOT / "data" / f"fault_insufficient_{run_ts}.db"
            old_db_path = prepare_isolated_db(scratch)
            record["isolated_db"] = str(scratch)
            log.event("INFO", case_id, "isolated-db", f"诊断写入独立库 {scratch}")

        record["backup"] = backup_configs(
            case_id,
            case.get("backup_devices") or [],
            devices,
            run_ts=run_ts,
            log=log,
        )
        if not record["backup"]["ok"]:
            raise ScenarioAbort(f"备份失败: {record['backup']['error']}")

        ping = case.get("ping")
        if ping:
            record["precheck_ping"] = poll_ping(
                devices,
                ping["target"],
                expect_success=True,
                case_id=case_id,
                log=log,
                step="precheck",
                timeout=PRECHECK_POLL_TIMEOUT_SECONDS,
            )
            if not record["precheck_ping"]["ok"]:
                raise ScenarioAbort(
                    f"注入前基线 ping {ping['target']} 未达 100%，故障会归因不清"
                )

        if case.get("inject"):
            record["injection"]["steps"] = apply_config_steps(
                case["inject"], devices, case_id=case_id, log=log, step_label="inject"
            )
            record["injection"]["performed"] = True
            injected = True

        if case.get("fault_checks"):
            record["fault_verification"]["checks"] = run_checks(
                case["fault_checks"], devices, case_id=case_id, log=log, step="verify-fault"
            )
        record["fault_verification"]["ok"] = all(
            item["ok"] for item in record["fault_verification"]["checks"]
        )
        if case.get("fault_checks") and not record["fault_verification"]["ok"]:
            raise ScenarioAbort("故障现象断言未通过，跳过采集与诊断以免污染评测结论")

        fault_target = case.get("fault_ping_expected_failure")
        if fault_target:
            record["fault_ping"] = poll_ping(
                devices,
                fault_target,
                expect_success=False,
                case_id=case_id,
                log=log,
                step="verify-fault",
                timeout=FAULT_POLL_TIMEOUT_SECONDS,
            )
            if not record["fault_ping"]["ok"]:
                raise ScenarioAbort(f"故障期 ping {fault_target} 未按要求失败")
        fault_verified = True
        injection_finished_at = _monotonic()
        record["injection"]["ok"] = record["injection"]["performed"] and record[
            "fault_verification"
        ]["ok"] and (record["fault_ping"] or {}).get("ok", True)
        record["injection"]["finished_at"] = _now_iso()

        if demo is not None:
            pause = demo.wait_for_operator(case, index=demo_index, total=demo_total)
            record["demo"] = {
                "mode": True,
                "app_base_url": demo.app_base_url(),
                "pause_seconds": pause["pause_seconds"],
                "interrupted": pause["interrupted"],
                "steps": [
                    "backup",
                    "inject",
                    "verify-fault",
                    "webapp-demo",
                    "repair",
                    "recheck",
                    "config-diff",
                ],
            }
            if pause["interrupted"]:
                record["failure_reasons"].append(
                    "演示期间操作员中断（Ctrl+C），已转入恢复流程"
                )
            else:
                # 演示模式下采集/诊断由操作员在 webapp 完成，这里不重复调用。
                diagnosis_ok = True
        else:
            record["collection"] = run_collection(
                case_id,
                case["collect_devices"],
                log=log,
                db_path=record["isolated_db"],
            )
            if not record["collection"]["ok"]:
                raise ScenarioAbort("采集失败或未返回 run_id")

            record["diagnosis"] = diagnose_case(case, expected, log=log)
            diagnosis_ok = True
    except ScenarioAbort as exc:
        record["failure_reasons"].append(str(exc))
        log.event("ERROR", case_id, "abort", str(exc))
    except Exception as exc:  # noqa: BLE001 - 单案例失败不能中断 --all
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["failure_reasons"].append(record["error"])
        log.event("ERROR", case_id, "error", record["error"])
    except KeyboardInterrupt:
        if demo is None:
            raise
        demo.interrupted = True
        block = record.get("demo") or {
            "mode": True,
            "app_base_url": demo.app_base_url(),
            "pause_seconds": 0.0,
            "steps": [],
        }
        block["interrupted"] = True
        record["demo"] = block
        record["failure_reasons"].append("演示期间收到 Ctrl+C，已转入恢复流程")
        log.event("WARN", case_id, "interrupt", "收到 Ctrl+C，转入恢复流程")
    finally:
        repair_started_at = _monotonic()
        if injected and case.get("repair"):
            try:
                record["repair"]["steps"] = apply_config_steps(
                    case["repair"], devices, case_id=case_id, log=log, step_label="repair"
                )
                record["repair"]["performed"] = True
                record["repair"]["ok"] = True
            except Exception as exc:  # noqa: BLE001
                record["repair"]["error"] = f"{type(exc).__name__}: {exc}"
                record["repair"]["ok"] = False
                log.event("ERROR", case_id, "repair", f"逆操作失败，必须人工介入: {exc}")
        elif injected:
            record["repair"]["error"] = "案例未定义逆操作命令"
            log.event("ERROR", case_id, "repair", "案例未定义逆操作命令，必须人工介入")

        ping = case.get("ping")
        if ping and injected and record["repair"]["ok"]:
            try:
                record["recheck_ping"] = poll_ping(
                    devices,
                    ping["target"],
                    expect_success=True,
                    case_id=case_id,
                    log=log,
                    step="recheck",
                    timeout=RECHECK_POLL_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                record["failure_reasons"].append(f"复测失败: {type(exc).__name__}: {exc}")
                log.event("ERROR", case_id, "recheck", f"复测异常: {exc}")

        if case.get("recovery_checks"):
            try:
                record["recovery_verification"]["checks"] = run_checks(
                    case["recovery_checks"],
                    devices,
                    case_id=case_id,
                    log=log,
                    step="verify-recovery",
                    poll_timeout=SHOW_RECHECK_POLL_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                record["recovery_verification"]["ok"] = False
                record["failure_reasons"].append(f"恢复确认失败: {type(exc).__name__}: {exc}")
                log.event("ERROR", case_id, "verify-recovery", f"恢复确认异常: {exc}")
            record["recovery_verification"]["ok"] = all(
                item["ok"] for item in record["recovery_verification"]["checks"]
            )

        if injected and record["repair"]["ok"]:
            try:
                record["recovery_verification"]["config_diff"] = verify_config_restored(
                    case, record, devices, log=log
                )
            except Exception as exc:  # noqa: BLE001
                record["failure_reasons"].append(
                    f"配置恢复比对失败: {type(exc).__name__}: {exc}"
                )
                log.event("ERROR", case_id, "config-diff", f"配置恢复比对异常: {exc}")

        if old_db_path is not None:
            restore_db_path(old_db_path)

    recovery_ok = True
    if injected:
        recovery_ok = (
            record["repair"]["ok"]
            and (record["recheck_ping"] or {}).get("ok", False)
            and (
                not case.get("recovery_checks")
                or record["recovery_verification"]["ok"]
            )
        )
    if not recovery_ok:
        record["failure_reasons"].append("恢复未确认")

    config_diffs = record["recovery_verification"].get("config_diff") or []
    config_restored = (
        all(item.get("matches") for item in config_diffs) if config_diffs else None
    )
    if config_restored is False:
        record["failure_reasons"].append(
            "恢复后 running-config 与注入前备份仍有差异（见 recovery_verification.config_diff）"
        )
    record["config_restored"] = config_restored

    pause_seconds = float((record.get("demo") or {}).get("pause_seconds") or 0.0)
    if injection_finished_at is not None and repair_started_at is not None:
        fault_window = max(0.0, repair_started_at - injection_finished_at)
        record["fault_window_s"] = round(fault_window, 1)
        record["fault_duration_s"] = round(max(0.0, fault_window - pause_seconds), 1)
    demo_interrupted = bool((record.get("demo") or {}).get("interrupted"))

    if not recovery_ok:
        record["status"] = "failed_recovery"
    elif demo_interrupted:
        record["status"] = "demo_interrupted"
    elif config_restored is False:
        record["status"] = "partial"
    elif (
        diagnosis_ok
        and fault_verified
        and config_restored is not False
        and not record["failure_reasons"]
    ):
        record["status"] = "success"
    record["finished_at"] = _now_iso()
    record["recovery_ok"] = recovery_ok
    record["duration_s"] = round(
        (datetime.now(timezone.utc) - datetime.fromisoformat(record["started_at"])).total_seconds(),
        1,
    )
    log.event(
        "INFO" if record["status"] == "success" else "ERROR",
        case_id,
        "done",
        f"status={record['status']} recovery_ok={recovery_ok} "
        f"config_restored={config_restored} reasons={record['failure_reasons'] or '无'}",
    )
    return record


def run_demo_sequence(
    cases: list[dict],
    *,
    demo: DemoSession,
    execute: Callable[[dict, int, int], dict],
    log: RunLog,
) -> list[dict]:
    """按队列推进演示：支持顺序、跳转、退出；恢复失败保持硬停止。"""
    by_id = {case["id"]: case for case in CASES}
    queue = [case["id"] for case in cases]
    total = len(queue)
    records: list[dict] = []
    index = 0
    while queue:
        index += 1
        case_id = queue.pop(0)
        case = by_id[case_id]
        demo.completed_ids.add(case_id)
        try:
            record = execute(case, index, total)
        except KeyboardInterrupt:
            demo.hard_interrupted = True
            demo.emit(
                [
                    "",
                    f"[演示] {case_id} 执行时被强制中断；脚本已尝试恢复，"
                    "请按 docs/TEST_RUNBOOK.md §10.6 人工复核设备。",
                    "",
                ]
            )
            log.event("ERROR", case_id, "interrupt", "执行被强制中断，请人工复核设备配置")
            break
        records.append(record)
        if record.get("status") == "failed_recovery":
            demo.emit(
                [
                    "",
                    f"[演示] {case_id} 恢复失败，演示中止；"
                    "请立即按 docs/TEST_RUNBOOK.md §10.6 人工核对设备配置。",
                    "",
                ]
            )
            log.event("ERROR", case_id, "stop", "恢复失败，演示中止，请人工核对设备")
            break
        if (record.get("demo") or {}).get("interrupted"):
            demo.emit(["", f"[演示] {case_id} 已恢复，退出演示（无遗留故障）。", ""])
            log.event("WARN", case_id, "demo-interrupt", "演示被中断，当前案例已恢复，退出")
            break
        demo.report_recovered(case, record, index=index, total=total)
        action, target = demo.ask_next(
            case, index=index, total=total, next_case_id=queue[0] if queue else None
        )
        if action == "quit":
            demo.emit(["", "已退出演示：当前案例已恢复，剩余案例跳过。", ""])
            log.event("INFO", case_id, "demo-quit", "操作员退出演示，剩余案例跳过（网络已恢复）")
            break
        if action == "jump" and target:
            if target in queue:
                queue.remove(target)
            queue.insert(0, target)
            log.event("INFO", case_id, "demo-jump", f"跳转到 {target}")
    return records


def write_summary(
    records: list[dict], *, log: RunLog, run_ts: str, mode: str = "auto"
) -> dict:
    """写 reports/fault_experiments_summary_<ts>.json；demo 模式额外汇总暂停信息。"""

    def variant_totals(key: str) -> dict:
        rows = [
            item["diagnosis"][key]
            for item in records
            if item.get("diagnosis") and item["diagnosis"].get(key)
        ]
        counts: dict[str, int] = {}
        for row in rows:
            label = row.get("correctness") or "unknown"
            counts[label] = counts.get(label, 0) + 1
        times = [
            row.get("total_response_time_ms")
            for row in rows
            if row.get("total_response_time_ms") is not None
        ]
        return {
            "cases": len(rows),
            "correctness_counts": counts,
            "avg_total_response_time_ms": (
                round(sum(times) / len(times), 1) if times else None
            ),
        }

    summary = {
        "generated_at": _now_iso(),
        "run_tag": run_ts,
        "mode": mode,
        "log_path": str(log.path),
        "case_count": len(records),
        "success_count": sum(1 for item in records if item["status"] == "success"),
        "by_variant": {
            "with_kb": variant_totals("with_kb"),
            "without_kb": variant_totals("without_kb"),
        },
        "demo": None,
        "cases": records,
    }
    if mode == "demo":
        pauses = [
            float((item.get("demo") or {}).get("pause_seconds") or 0.0)
            for item in records
        ]
        summary["demo"] = {
            "cases": [item["case_id"] for item in records],
            "pause_seconds_total": round(sum(pauses), 1),
            "interrupted": any(
                (item.get("demo") or {}).get("interrupted") for item in records
            ),
        }
    summary_path = REPORTS_DIR / f"fault_experiments_summary_{run_ts}.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.event("INFO", None, "summary", f"汇总写入 {summary_path}（mode={mode}）")
    return summary


def collect_baseline(devices: dict[str, dict], *, log: RunLog, only: list[str] | None = None) -> int:
    from collector.whitelist_commands import get_collection_commands

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    names = only or [name for name, item in devices.items() if item.get("device_type") == "cisco_ios"]
    failures = 0
    for name in names:
        device = require_device(devices, name)
        if device.get("device_type") != "cisco_ios":
            log.event("WARN", name, "baseline", "跳过非 Cisco 网络设备")
            continue
        commands_map = get_collection_commands(device)
        plan = [
            ("running_config", commands_map.get("running_config")),
            ("vlan_brief", commands_map.get("vlan_brief")),
            ("route_table", commands_map.get("route_table")),
        ]
        sections = [
            f"# device: {name}",
            f"# mgmt_ip: {device.get('mgmt_ip')}",
            f"# model: {device.get('model')}",
            f"# captured_at: {_now_iso()}",
            "",
        ]
        ok = True
        try:
            conn = connect(device, case_id=name, log=log)
            try:
                for label, command in plan:
                    if not command:
                        sections.append(f"===== {label}: {name} 不支持该命令，已跳过")
                        sections.append("")
                        continue
                    output = conn.send_command(command, read_timeout=60)
                    sections.append(f"===== {label} :: {command}")
                    sections.append(output.rstrip())
                    sections.append("")
            finally:
                conn.disconnect()
        except Exception as exc:  # noqa: BLE001
            ok = False
            failures += 1
            sections.append(f"===== 采集失败: {type(exc).__name__}: {exc}")
            log.event("ERROR", name, "baseline", f"采集失败: {type(exc).__name__}: {exc}")
        path = BASELINE_DIR / f"{name}.txt"
        path.write_text("\n".join(sections), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if ok:
            log.event("INFO", name, "baseline", f"已写入 {path}")
    return failures


CASES: list[dict] = [
    {
        "id": "LINK-1",
        "external_id": "LIVE_LINK_1",
        "category": "link_down",
        "description": "BR-A-SW2 Ethernet0/0（上联 BR-A-R2 的 trunk）shutdown，分支A接入链路中断",
        "device": "BR-A-SW2",
        "backup_devices": ["BR-A-R2", "BR-A-SW2"],
        "collect_devices": ["BR-A-R2", "BR-A-SW2"],
        "question": "分支A路由器和交换机之间好像不通了，能帮忙看看是什么问题吗？",
        "expected_notes": "应指出 BR-A-SW2 的 Ethernet0/0 处于 administratively down/down、trunk 消失，判断为分支A路由器-交换机互联链路中断。",
        "inject": [
            {"device": "BR-A-SW2", "commands": ["interface Ethernet0/0", "shutdown"]}
        ],
        "repair": [
            {"device": "BR-A-SW2", "commands": ["interface Ethernet0/0", "no shutdown"]}
        ],
        "fault_checks": [
            {
                "device": "BR-A-SW2",
                "command": "show ip interface brief",
                "type": "line",
                "prefix": "Ethernet0/0",
                "contains": ["administratively down"],
                "not_contains": [" up "],
            },
            {
                "device": "BR-A-SW2",
                "command": "show interfaces trunk",
                "type": "text",
                "not_contains": ["Et0/0"],
            },
        ],
        "recovery_checks": [
            {
                "device": "BR-A-SW2",
                "command": "show ip interface brief",
                "type": "line",
                "prefix": "Ethernet0/0",
                "contains": ["up"],
                "not_contains": ["administratively down"],
            },
            {
                "device": "BR-A-SW2",
                "command": "show interfaces trunk",
                "type": "text",
                "contains": ["Et0/0"],
            },
        ],
        "fault_ping_expected_failure": "10.20.10.11",
        "ping": {"target": "10.20.10.11"},
    },
    {
        "id": "LINK-2",
        "external_id": "LIVE_LINK_2",
        "category": "link_down",
        "description": "BR-B-SW3 Ethernet0/0（上联 BR-B-R3 的 trunk）shutdown，分支B接入链路中断",
        "device": "BR-B-SW3",
        "backup_devices": ["BR-B-R3", "BR-B-SW3"],
        "collect_devices": ["BR-B-R3", "BR-B-SW3"],
        "question": "分支B的交换机和路由器之间好像断了，分支B终端访问总部一直失败，帮我诊断一下。",
        "expected_notes": "应指出 BR-B-SW3 的 Ethernet0/0 被关闭（administratively down）、trunk 消失，判断为分支B接入链路中断。",
        "inject": [
            {"device": "BR-B-SW3", "commands": ["interface Ethernet0/0", "shutdown"]}
        ],
        "repair": [
            {"device": "BR-B-SW3", "commands": ["interface Ethernet0/0", "no shutdown"]}
        ],
        "fault_checks": [
            {
                "device": "BR-B-SW3",
                "command": "show ip interface brief",
                "type": "line",
                "prefix": "Ethernet0/0",
                "contains": ["administratively down"],
                "not_contains": [" up "],
            },
            {
                "device": "BR-B-SW3",
                "command": "show interfaces trunk",
                "type": "text",
                "not_contains": ["Et0/0"],
            },
        ],
        "recovery_checks": [
            {
                "device": "BR-B-SW3",
                "command": "show ip interface brief",
                "type": "line",
                "prefix": "Ethernet0/0",
                "contains": ["up"],
                "not_contains": ["administratively down"],
            },
            {
                "device": "BR-B-SW3",
                "command": "show interfaces trunk",
                "type": "text",
                "contains": ["Et0/0"],
            },
        ],
        "fault_ping_expected_failure": "10.30.10.11",
        "ping": {"target": "10.30.10.11"},
    },
    {
        "id": "VLAN-1",
        "external_id": "LIVE_VLAN_1",
        "category": "vlan_misconfig",
        "description": "BR-A-SW2 Ethernet1/0 由 access VLAN10 误改为 VLAN20",
        "device": "BR-A-SW2",
        "backup_devices": ["BR-A-SW2"],
        "collect_devices": ["BR-A-SW2"],
        "question": "分支A的测试终端连不上同VLAN的其他设备，怀疑是VLAN配置问题，帮我看一下。",
        "expected_notes": "应指出 BR-A-SW2 的 Ethernet1/0 被划入 VLAN20（应为 VLAN10），属接入端口 VLAN 错配。",
        "inject": [
            {
                "device": "BR-A-SW2",
                "commands": ["interface Ethernet1/0", "switchport access vlan 20"],
            }
        ],
        "repair": [
            {
                "device": "BR-A-SW2",
                "commands": ["interface Ethernet1/0", "switchport access vlan 10"],
            }
        ],
        "fault_checks": [
            {
                "device": "BR-A-SW2",
                "command": "show vlan brief",
                "type": "vlan_ports",
                "vlan_id": 10,
                "port": "Ethernet1/0",
                "expect_member": False,
            },
            {
                "device": "BR-A-SW2",
                "command": "show vlan brief",
                "type": "vlan_ports",
                "vlan_id": 20,
                "port": "Ethernet1/0",
                "expect_member": True,
            },
            {
                "device": "BR-A-SW2",
                "command": "show running-config interface Ethernet1/0",
                "type": "text",
                "contains": ["switchport access vlan 20"],
            },
        ],
        "recovery_checks": [
            {
                "device": "BR-A-SW2",
                "command": "show vlan brief",
                "type": "vlan_ports",
                "vlan_id": 10,
                "port": "Ethernet1/0",
                "expect_member": True,
            },
            {
                "device": "BR-A-SW2",
                "command": "show vlan brief",
                "type": "vlan_ports",
                "vlan_id": 20,
                "port": "Ethernet1/0",
                "expect_member": False,
            },
            {
                "device": "BR-A-SW2",
                "command": "show running-config interface Ethernet1/0",
                "type": "text",
                "contains": ["switchport access vlan 10"],
            },
        ],
        "fault_ping_expected_failure": "10.20.10.11",
        "ping": {"target": "10.20.10.11"},
    },
    {
        "id": "VLAN-2",
        "external_id": "LIVE_VLAN_2",
        "category": "vlan_misconfig",
        "description": "BR-A-SW2 Ethernet0/0 trunk 上移除 allowed VLAN10",
        "device": "BR-A-SW2",
        "backup_devices": ["BR-A-SW2"],
        "collect_devices": ["BR-A-SW2"],
        "question": "总部和分支A之间的trunk是不是没放通VLAN10？两边终端互相看不到，帮我确认。",
        "expected_notes": "应指出 BR-A-SW2 的 Ethernet0/0 trunk 放通列表缺少 VLAN10，导致 VLAN10 流量无法通过上联。",
        "notes": "TODO: 采集侧目前只把 running-config 折叠为行数+sha256，trunk allowed vlan 明细不会进入提示词证据；该案例的可命中性依赖模型结合 ping 失败推断，如需稳定命中需扩展 state_parser/evidence_builder。基线（2026-11-13 实读）BR-A-SW2 Et0/0 没有显式 allowed vlan 行（默认 1-4094）；remove 10 后配置会显式写出 1-9,11-4094，逆操作先 add 10，再用可选步骤 no switchport trunk allowed vlan 清理残留行。",
        "inject": [
            {
                "device": "BR-A-SW2",
                "commands": [
                    "interface Ethernet0/0",
                    "switchport trunk allowed vlan remove 10",
                ],
            }
        ],
        "repair": [
            {
                "device": "BR-A-SW2",
                "commands": [
                    "interface Ethernet0/0",
                    "switchport trunk allowed vlan add 10",
                ],
            },
            {
                "device": "BR-A-SW2",
                "commands": [
                    "interface Ethernet0/0",
                    "no switchport trunk allowed vlan",
                ],
                "optional": True,
            },
        ],
        "fault_checks": [
            {
                "device": "BR-A-SW2",
                "command": "show interfaces trunk",
                "type": "trunk_vlans",
                "port": "Ethernet0/0",
                "vlan_id": 10,
                "expect_allowed": False,
            },
        ],
        "recovery_checks": [
            {
                "device": "BR-A-SW2",
                "command": "show interfaces trunk",
                "type": "trunk_vlans",
                "port": "Ethernet0/0",
                "vlan_id": 10,
                "expect_allowed": True,
            },
        ],
        "fault_ping_expected_failure": "10.20.10.11",
        "ping": {"target": "10.20.10.11"},
    },
    {
        "id": "ROUTE-1",
        "external_id": "LIVE_ROUTE_1",
        "category": "default_route_missing",
        "description": "BR-B-R3 删除默认路由 0.0.0.0/0 via 172.16.2.1",
        "device": "BR-B-R3",
        "backup_devices": ["BR-B-R3"],
        "collect_devices": ["BR-B-R3"],
        "question": "分支B的终端访问总部服务器一直超时，路由是不是有问题？",
        "expected_notes": "应指出 BR-B-R3 缺少默认路由（Gateway of last resort is not set），回包无路可走导致分支B访问总部超时。",
        "inject": [
            {
                "device": "BR-B-R3",
                "commands": ["no ip route 0.0.0.0 0.0.0.0 172.16.2.1"],
            }
        ],
        "repair": [
            {
                "device": "BR-B-R3",
                "commands": ["ip route 0.0.0.0 0.0.0.0 172.16.2.1"],
            }
        ],
        "fault_checks": [
            {
                "device": "BR-B-R3",
                "command": "show ip route",
                "type": "text",
                "contains": ["Gateway of last resort is not set"],
                "not_contains": ["0.0.0.0/0"],
            },
        ],
        "recovery_checks": [
            {
                "device": "BR-B-R3",
                "command": "show ip route",
                "type": "text",
                "contains": ["0.0.0.0/0"],
                "not_contains": ["Gateway of last resort is not set"],
            },
        ],
        "fault_ping_expected_failure": "10.30.10.11",
        "ping": {"target": "10.30.10.11"},
    },
    {
        "id": "ROUTE-2",
        "external_id": "LIVE_ROUTE_2",
        "category": "default_route_missing",
        "description": "BR-A-R2 删除默认路由 0.0.0.0/0 via 172.16.1.1",
        "device": "BR-A-R2",
        "backup_devices": ["BR-A-R2"],
        "collect_devices": ["BR-A-R2"],
        "question": "分支A访问不了总部和外网的其他网段，但到分支B的路由看起来正常，什么情况？",
        "expected_notes": "应指出 BR-A-R2 缺少默认路由，需恢复到 172.16.1.1 的静态默认路由；不应把 OSPF 邻居当成根因。",
        "inject": [
            {
                "device": "BR-A-R2",
                "commands": ["no ip route 0.0.0.0 0.0.0.0 172.16.1.1"],
            }
        ],
        "repair": [
            {
                "device": "BR-A-R2",
                "commands": ["ip route 0.0.0.0 0.0.0.0 172.16.1.1"],
            }
        ],
        "fault_checks": [
            {
                "device": "BR-A-R2",
                "command": "show ip route",
                "type": "text",
                "contains": ["Gateway of last resort is not set"],
                "not_contains": ["0.0.0.0/0"],
            },
        ],
        "recovery_checks": [
            {
                "device": "BR-A-R2",
                "command": "show ip route",
                "type": "text",
                "contains": ["0.0.0.0/0"],
                "not_contains": ["Gateway of last resort is not set"],
            },
        ],
        "fault_ping_expected_failure": "10.20.10.11",
        "ping": {"target": "10.20.10.11"},
    },
    {
        "id": "ACL-1",
        "external_id": "LIVE_ACL_1",
        "category": "acl_overblock",
        "description": "HQ-R1 WAN-IN 插入 5 deny ip 10.20.10.0/24 -> 10.10.10.0/24",
        "device": "HQ-R1",
        "backup_devices": ["HQ-R1"],
        "collect_devices": ["HQ-R1"],
        "question": "分支A终端突然访问不了总部服务器了，之前一直是通的，怀疑是不是ACL改动导致的。",
        "expected_notes": "应指出 HQ-R1 的 WAN-IN 第 5 条 deny 拦截了分支A 10.20.10.0/24 到总部服务器 10.10.10.0/24 的流量。",
        "inject": [
            {
                "device": "HQ-R1",
                "commands": [
                    "ip access-list extended WAN-IN",
                    "5 deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255",
                ],
            }
        ],
        "repair": [
            {"device": "HQ-R1", "commands": ["ip access-list extended WAN-IN", "no 5"]}
        ],
        "fault_checks": [
            {
                "device": "HQ-R1",
                "command": "show access-lists WAN-IN",
                "type": "text",
                "contains": ["5 deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255"],
            },
        ],
        "recovery_checks": [
            {
                "device": "HQ-R1",
                "command": "show access-lists WAN-IN",
                "type": "text",
                "not_contains": ["deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255"],
                "contains": ["permit ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255"],
            },
        ],
        "fault_ping_expected_failure": "10.20.10.11",
        "ping": {"target": "10.20.10.11"},
    },
    {
        "id": "ACL-2",
        "external_id": "LIVE_ACL_2",
        "category": "acl_overblock",
        "description": "BR-A-R2 Ethernet1/0 入向新建 HQ-IN，误 deny 10.10.10.0/24 -> 10.20.10.0/24",
        "device": "BR-A-R2",
        "backup_devices": ["BR-A-R2"],
        "collect_devices": ["BR-A-R2", "HQ-R1"],
        "question": "分支A和总部之间突然只有单向业务异常，总部侧看起来正常，怀疑是分支A路由器上有ACL误拦截，帮我定位。",
        "expected_notes": "应指出 BR-A-R2 的 Ethernet1/0 入向 ACL HQ-IN 中的 deny 规则拦截了总部 10.10.10.0/24 到分支A 10.20.10.0/24 的流量。",
        "notes": "TODO: 该 ACL 是本脚本新建后挂载，逆操作按 先摘接口、再删 ACL 的顺序还原；若设备 Et1/0 原本已有 ip access-group，必须先跑 --collect-baseline 核对并调整本案例。",
        "inject": [
            {
                "device": "BR-A-R2",
                "commands": [
                    "ip access-list extended HQ-IN",
                    "10 deny ip 10.10.10.0 0.0.0.255 10.20.10.0 0.0.0.255",
                    "20 permit ip any any",
                ],
            },
            {
                "device": "BR-A-R2",
                "commands": ["interface Ethernet1/0", "ip access-group HQ-IN in"],
            },
        ],
        "repair": [
            {
                "device": "BR-A-R2",
                "commands": ["interface Ethernet1/0", "no ip access-group HQ-IN in"],
            },
            {
                "device": "BR-A-R2",
                "commands": ["no ip access-list extended HQ-IN"],
            },
        ],
        "fault_checks": [
            {
                "device": "BR-A-R2",
                "command": "show access-lists HQ-IN",
                "type": "text",
                "contains": ["10 deny ip 10.10.10.0 0.0.0.255 10.20.10.0 0.0.0.255"],
            },
            {
                "device": "BR-A-R2",
                "command": "show running-config interface Ethernet1/0",
                "type": "text",
                "contains": ["ip access-group HQ-IN in"],
            },
        ],
        "recovery_checks": [
            {
                "device": "BR-A-R2",
                "command": "show access-lists",
                "type": "text",
                "not_contains": ["access list HQ-IN"],
            },
            {
                "device": "BR-A-R2",
                "command": "show running-config interface Ethernet1/0",
                "type": "text",
                "not_contains": ["ip access-group"],
            },
        ],
        "fault_ping_expected_failure": "10.20.10.11",
        "ping": {"target": "10.20.10.11"},
    },
    {
        "id": "NORMAL-1",
        "external_id": "LIVE_NORMAL_1",
        "category": "normal",
        "description": "无注入对照：全量采集后检查总部-分支A是否正常",
        "device": None,
        "backup_devices": ["HQ-R1", "BR-A-R2", "BR-A-SW2"],
        "collect_devices": [
            "HQ-R1",
            "BR-A-R2",
            "BR-B-R3",
            "HQ-SW1",
            "BR-A-SW2",
            "BR-B-SW3",
        ],
        "question": "帮我检查一下总部和分支A之间的网络现在是否正常。",
        "expected_notes": "证据应全部正常：接口 up、默认路由存在、VLAN 正常、ping 成功；不应编造故障。",
        "inject": [],
        "repair": [],
        "fault_checks": [],
        "recovery_checks": [],
        "ping": {"target": "10.20.10.11"},
    },
    {
        "id": "INSUFFICIENT-1",
        "external_id": "LIVE_INSUFFICIENT_1",
        "category": "insufficient_evidence",
        "description": "证据不足对照：只采集 HQ-R1，却要求诊断 BR-B-SW3 的端口状态",
        "device": "BR-B-SW3",
        "backup_devices": ["HQ-R1"],
        "collect_devices": ["HQ-R1"],
        "isolated_db": True,
        "question": "分支B交换机 BR-B-SW3 是不是有端口被误关闭了？请分析原因并给出修复步骤。",
        "expected_notes": "BR-B-SW3 没有任何已发布采集结果，模型应在 missing_information 说明缺少该设备证据，不能编造具体端口或断言已 shutdown。",
        "inject": [],
        "repair": [],
        "fault_checks": [],
        "recovery_checks": [],
        "ping": None,
    },
]


def _find_case(case_id: str) -> dict:
    for case in CASES:
        if case["id"].lower() == case_id.lower():
            return case
    raise ConfigurationError(
        f"未知案例 {case_id}，可选: {', '.join(case['id'] for case in CASES)}"
    )


def demo_cases() -> list[dict]:
    """演示模式只覆盖 8 个注入案例，对照案例（NORMAL-1/INSUFFICIENT-1）不参与。"""
    cases = [case for case in CASES if case.get("inject")]
    if len(cases) != DEMO_CASE_COUNT:
        raise ConfigurationError(
            f"演示模式需要 {DEMO_CASE_COUNT} 个注入案例，当前 {len(cases)} 个"
        )
    return cases


def select_demo_cases(case_id: str | None) -> list[dict]:
    cases = demo_cases()
    if not case_id:
        return cases
    target = _find_case(case_id)
    if not target.get("inject"):
        raise ConfigurationError(f"{case_id} 是对照案例（不注入），不能用于 --demo")
    return [target]


def match_demo_case(token: str) -> str | None:
    """允许 ``VLAN-2`` / ``vlan2`` / ``VLAN_2`` 这类输入。"""
    normalized = re.sub(r"[\s_-]+", "", (token or "")).upper()
    if not normalized:
        return None
    for case in demo_cases():
        if re.sub(r"[\s_-]+", "", case["id"]).upper() == normalized:
            return case["id"]
    return None


def print_demo_plan(cases: list[dict], demo: DemoSession) -> None:
    urls = demo.endpoints()
    print("demo 模式计划（不会连接设备、不会改配置）")
    print(f"控制台总览: {urls['overview']}")
    print(f"设备资产:   {urls['assets']}")
    print(f"诊断页:     {urls['diagnosis']}")
    print(f"采集 API:   POST {urls['collection_api']}")
    print()
    for index, case in enumerate(cases, 1):
        print_case_plan(case)
        print("  demo: 跳过自动采集/诊断（由评委在 webapp 完成后按 Enter），随后自动恢复")
        print(
            f"  demo 暂停点: 第 {index}/{len(cases)} 个案例故障验证通过后等待回车，"
            "回车即自动恢复"
        )
    print()
    print("对照案例 NORMAL-1 / INSUFFICIENT-1 不参与 demo；Ctrl+C 会先恢复再退出。")


def validate_case_table() -> None:
    """案例表与期望词表必须一一对应，注入命令必须通过安全护栏。"""
    ids = [case["id"] for case in CASES]
    if len(ids) != len(set(ids)):
        raise ConfigurationError("案例 ID 有重复")
    missing = [case_id for case_id in ids if case_id not in LIVE_EXPECTED_BY_ID]
    extra = [case_id for case_id in LIVE_EXPECTED_BY_ID if case_id not in ids]
    if missing or extra:
        raise ConfigurationError(f"案例与期望词表不一致: 缺少={missing} 多余={extra}")
    injected = [case for case in CASES if case.get("inject")]
    if len(injected) != DEMO_CASE_COUNT:
        raise ConfigurationError(
            f"注入案例应为 {DEMO_CASE_COUNT} 个，当前 {len(injected)} 个"
        )
    for case in CASES:
        for step in (case.get("inject") or []) + (case.get("repair") or []):
            guard_commands(step["commands"], purpose=f"{case['id']}/case-table")
        if not case.get("collect_devices"):
            raise ConfigurationError(f"{case['id']} 未声明采集设备")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="EVE-NG 真实故障注入 + RAG 诊断评测")
    action = parser.add_mutually_exclusive_group(required=False)
    action.add_argument("--list", action="store_true", help="列出全部案例")
    action.add_argument("--case", metavar="ID", help="执行单个案例")
    action.add_argument("--all", action="store_true", help="顺序执行全部案例")
    action.add_argument("--collect-baseline", action="store_true", help="只读抓取 6 台设备基线")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="人工演示模式：顺序演示 8 个注入案例，每个案例注入后暂停等待回车",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不连接设备")
    parser.add_argument(
        "--devices",
        help="配合 --collect-baseline 限定设备（逗号分隔）",
    )
    parser.add_argument(
        "--run-tag",
        default=None,
        help="覆盖报告时间戳（默认当前本地时间 yyyymmddTHHMMSS）",
    )
    args = parser.parse_args(argv)
    if args.demo and args.collect_baseline:
        parser.error("--demo 不能与 --collect-baseline 同时使用")
    if args.demo and args.list:
        parser.error("--demo 不能与 --list 同时使用")
    if not any([args.list, args.case, args.all, args.collect_baseline, args.demo]):
        parser.error("必须指定 --list / --case / --all / --collect-baseline / --demo 之一")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.list:
        for case in CASES:
            flag = "注入" if case.get("inject") else "对照"
            print(f"{case['id']:<14} {case['category']:<22} [{flag}] {case['description']}")
        print(f"\n共 {len(CASES)} 个案例（8 个故障 + 2 个对照）")
        return 0

    run_ts = args.run_tag or datetime.now().strftime("%Y%m%dT%H%M%S")
    log = RunLog(REPORTS_DIR / f"fault_experiments_{run_ts}.log")
    try:
        devices = load_devices()
        validate_case_table()

        if args.demo:
            demo_selection = select_demo_cases(args.case)
            demo = DemoSession(log=log)
            if args.dry_run:
                print_demo_plan(demo_selection, demo)
                log.event(
                    "INFO", None, "demo-dry-run", f"打印 {len(demo_selection)} 个演示计划"
                )
                return 0
            require_credentials()
            demo.announce_start(len(demo_selection))
            records: list[dict] = []
            try:
                records = run_demo_sequence(
                    demo_selection,
                    demo=demo,
                    execute=lambda case, index, total: execute_case(
                        case,
                        devices,
                        log=log,
                        run_ts=run_ts,
                        demo=demo,
                        demo_index=index,
                        demo_total=total,
                    ),
                    log=log,
                )
            except KeyboardInterrupt:
                print(
                    "\n演示被强制中断：若当前案例已注入，脚本已尝试恢复；"
                    "请按 docs/TEST_RUNBOOK.md §10.6 复核设备配置。",
                    flush=True,
                )
                log.event("ERROR", None, "demo-interrupt", "演示被强制中断，请人工复核设备配置")
                write_summary(records, log=log, run_ts=run_ts, mode="demo")
                return 130
            summary = write_summary(records, log=log, run_ts=run_ts, mode="demo")
            failures = [
                item
                for item in records
                if item.get("status") not in ("success", "demo_interrupted")
            ]
            log.event(
                "INFO" if not failures else "ERROR",
                None,
                "demo-done",
                f"演示完成 {len(records)} 个案例，异常 {len(failures)} 个，"
                f"暂停合计 {summary['demo']['pause_seconds_total']}s",
            )
            if demo.hard_interrupted:
                return 130
            return 0 if not failures else 1

        if args.collect_baseline:
            log.event("INFO", None, "baseline", "开始只读抓取设备基线")
            only = (
                [name.strip() for name in args.devices.split(",") if name.strip()]
                if args.devices
                else None
            )
            failures = collect_baseline(devices, log=log, only=only)
            log.event(
                "INFO" if failures == 0 else "ERROR",
                None,
                "baseline",
                f"完成，失败设备数={failures}，输出目录={BASELINE_DIR}",
            )
            return 0 if failures == 0 else 1

        if args.dry_run:
            selected = CASES if args.all else [_find_case(args.case)]
            print(f"dry-run：不会连接设备，也不会写数据库。报告时间戳 {run_ts}")
            for case in selected:
                print_case_plan(case)
            log.event("INFO", None, "dry-run", f"打印 {len(selected)} 个案例的执行计划")
            return 0

        require_credentials()
        selected = CASES if args.all else [_find_case(args.case)]
        log.event(
            "INFO",
            None,
            "start",
            f"开始 {len(selected)} 个案例，日志 {log.path}",
        )

        records = []
        hard_stop = False
        for case in selected:
            if hard_stop:
                records.append(
                    {
                        "case_id": case["id"],
                        "external_id": case["external_id"],
                        "category": case["category"],
                        "status": "skipped",
                        "failure_reasons": ["前一案例恢复失败，已中止后续注入"],
                    }
                )
                log.event("ERROR", case["id"], "skip", "前一案例恢复失败，跳过")
                continue
            record = execute_case(case, devices, log=log, run_ts=run_ts)
            records.append(record)
            if record["status"] == "failed_recovery":
                hard_stop = True
                log.event("ERROR", None, "stop", "检测到恢复失败，停止后续案例，请人工核对设备配置")

        summary = write_summary(records, log=log, run_ts=run_ts, mode="auto")
        return 0 if summary["success_count"] == len(records) else 1
    except ConfigurationError as exc:
        log.event("ERROR", None, "config", str(exc))
        return 2
    except UnsafeCommandError as exc:
        log.event("ERROR", None, "safety", f"安全检查未通过: {exc}")
        return 2
    finally:
        log.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
