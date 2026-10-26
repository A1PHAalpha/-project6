# 采集命令配置与输出校验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让采集器按设备角色选择命令，并把 Cisco 命令错误提示判为失败，避免污染证据。

**Architecture:** 在 `collector/whitelist_commands.py` 增加按设备记录生成采集命令集的函数；在 `collector/collector_ssh.py` 使用该函数，并在命令输出后检查 Cisco 错误标记。安全白名单本身保持不变。

**Tech Stack:** Python 3.12、Netmiko、pytest。

**Spec:** `docs/superpowers/specs/2026-10-26-collector-command-profiles-design.md`

## Global Constraints

- `is_readonly_command()` 的既有安全语义不变。
- 路由器/交换机判断基于 `model` 和 `role`，不新增数据库字段。
- 无法判断角色时保留完整命令集，避免误删能力。
- 错误输出保留到 `raw_output`，状态记为 `failed`。
- 不改 SNMP 采集和采集批次结构。

## Review Focus

- `model=IOSv` 或 `role` 含“路由器”的设备不能收到 `show vlan brief`。测试在 Task 1。
- `model=IOSvL2` 或 `role` 含“交换机”的设备必须保留 `show vlan brief`。测试在 Task 1。
- 正常输出中的普通文字不能被误判为错误；只有明确的 Cisco 错误前缀才失败。测试在 Task 2。
- 命令返回 `% Invalid input` 时，结果必须是 `failed`，并保留原始输出。测试在 Task 2。

---

### Task 1: 按设备角色生成采集命令集

**Files:**
- Modify: `collector/whitelist_commands.py`
- Modify: `collector/collector_ssh.py`
- Create: `tests/test_collector_commands.py`

**Interfaces:**
- Produces:
  - `get_collection_commands(device: dict) -> dict[str, str]`
- Consumes: `READONLY_COMMANDS`。

- [ ] **Step 1: 写失败测试**

```python
from collector.whitelist_commands import get_collection_commands


def test_router_omits_vlan_brief():
    commands = get_collection_commands(
        {
            "device_type": "cisco_ios",
            "model": "IOSv (EVE-NG)",
            "role": "总部核心路由器",
        }
    )
    assert "vlan_brief" not in commands
    assert "interface_status" in commands


def test_switch_keeps_vlan_brief():
    commands = get_collection_commands(
        {
            "device_type": "cisco_ios",
            "model": "IOSvL2 (EVE-NG)",
            "role": "总部核心交换机",
        }
    )
    assert commands["vlan_brief"] == "show vlan brief"


def test_unknown_profile_keeps_full_command_set():
    commands = get_collection_commands({"device_type": "cisco_ios"})
    assert "vlan_brief" in commands
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_collector_commands.py -q
```

Expected: `ImportError`，函数不存在。

- [ ] **Step 3: 实现命令集选择**

`get_collection_commands()` 复制 `READONLY_COMMANDS[device_type]`；
当 `device_type == "cisco_ios"` 且明确是路由器时，移除 `vlan_brief`；
交换机或无法判断时保留完整命令集。

`collect_device()` 改用 `get_collection_commands(device)`。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_collector_commands.py -q
```

Expected: PASS。

---

### Task 2: Cisco 命令错误输出校验

**Files:**
- Modify: `collector/collector_ssh.py`
- Modify: `tests/test_collector_commands.py`

**Interfaces:**
- Produces:
  - `is_invalid_command_output(output: str) -> bool`
  - 采集结果新增失败码 `invalid_command_output`。

- [ ] **Step 1: 写失败测试**

```python
from collector.collector_ssh import is_invalid_command_output


def test_cisco_invalid_input_is_detected():
    assert is_invalid_command_output("% Invalid input detected at '^' marker.") is True


def test_cisco_incomplete_and_ambiguous_are_detected():
    assert is_invalid_command_output("% Incomplete command.") is True
    assert is_invalid_command_output("% Ambiguous command: \"sh v\"") is True


def test_normal_output_is_not_invalid():
    assert is_invalid_command_output("Interface  IP-Address  Status\nEt0/0  10.0.0.1  up") is False
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_collector_commands.py -q
```

Expected: `ImportError` 或函数不存在。

- [ ] **Step 3: 实现输出校验**

在 `collect_device()` 的成功分支中检查 `is_invalid_command_output(safe_output)`。
命中时返回：

```python
{
    "status": "failed",
    "error_code": "invalid_command_output",
    "fail_reason": "设备返回命令错误提示",
    "raw_output": safe_output,
    ...
}
```

- [ ] **Step 4: 写采集器集成测试**

用假的 Netmiko 连接返回 `% Invalid input`，断言对应采集项 `status=failed`、
`error_code=invalid_command_output`，且 `raw_output` 保留。

- [ ] **Step 5: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_collector_commands.py tests/test_collection_run.py -q
```

Expected: PASS。

---

### Task 3: 真实采集回归

**Files:**
- No production changes expected.

- [ ] **Step 1: 真实采集 HQ-R1 + HQ-SW1**

使用临时 SQLite 库，确认：

- HQ-R1 不再执行 `show vlan brief`；
- HQ-R1 SSH 命令全部成功；
- HQ-SW1 仍执行 `show vlan brief`；
- 失败命令如果有，带 `invalid_command_output`。

- [ ] **Step 2: 全量回归**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm test && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add collector/whitelist_commands.py collector/collector_ssh.py tests/test_collector_commands.py docs/superpowers/specs/2026-10-26-collector-command-profiles-design.md docs/superpowers/plans/2026-10-26-collector-command-profiles.md
git commit -m "fix(collector): use role-specific commands and reject invalid output"
```
