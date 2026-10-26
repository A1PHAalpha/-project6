# 采集 SSH 连接重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对瞬态 SSH 连接失败重试一次，避免偶发超时污染设备可达性。

**Architecture:** 在 `collector/collector_ssh.py` 增加 `_connect_with_retry()`；`collect_device()` 和 `execute_ssh_command()` 统一使用。认证异常直接抛出，其他连接异常按配置重试。

**Tech Stack:** Python 3.12、Netmiko、pytest。

**Spec:** `docs/superpowers/specs/2026-10-26-ssh-connection-retry-design.md`

## Global Constraints

- 默认 `SSH_CONNECT_RETRIES=1`（首次之外再试 1 次）。
- 默认 `SSH_RETRY_DELAY_SECONDS=1.0`，最大重试 5 次。
- `NetmikoAuthenticationException` 不重试。
- 保持 `collect_device()` 返回结构和 `ssh_connection_failed` 错误码不变。
- 测试中不产生真实等待。

## Review Focus

- 第一次超时、第二次成功时，必须返回成功结果，不能把第一次异常暴露给调用方。测试在 Task 1。
- 认证失败必须只调用一次连接函数，避免对错误密码反复尝试。测试在 Task 1。
- 重试次数为 0 时必须只尝试一次。测试在 Task 1。
- 重试耗尽后仍返回原有 `ssh_connection_failed`，不能让异常冒泡。测试在 Task 1。

---

### Task 1: 连接重试

**Files:**
- Modify: `collector/collector_ssh.py`
- Modify: `tests/test_collector_commands.py`

**Interfaces:**
- Produces:
  - `_connect_with_retry(params: dict, *, device_name: str = "device") -> ConnectHandler`
  - `_connect_attempts() -> int`
- Consumes: `ConnectHandler`、`NetmikoAuthenticationException`、`NetmikoTimeoutException`。

- [ ] **Step 1: 写失败测试**

```python
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from collector import collector_ssh


class FakeConnection:
    def send_command(self, command, read_timeout=15):
        return "ok"

    def disconnect(self):
        pass


def test_collect_device_retries_transient_connection_failure(monkeypatch):
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise NetmikoTimeoutException("No existing session")
        return FakeConnection()

    monkeypatch.setattr(collector_ssh, "ConnectHandler", fake_connect)
    monkeypatch.setattr(collector_ssh.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("SSH_CONNECT_RETRIES", "1")
    monkeypatch.setenv("SSH_RETRY_DELAY_SECONDS", "0")

    device = {
        "device_name": "HQ-R1",
        "device_type": "cisco_ios",
        "model": "IOSv (EVE-NG)",
        "role": "总部核心路由器",
        "mgmt_ip": "192.0.2.1",
    }
    results = collector_ssh.collect_device(device, "admin", "password")

    assert len(calls) == 2
    assert all(result["status"] == "success" for result in results)


def test_collect_device_does_not_retry_authentication_failure(monkeypatch):
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        raise NetmikoAuthenticationException("bad password")

    monkeypatch.setattr(collector_ssh, "ConnectHandler", fake_connect)
    monkeypatch.setenv("SSH_CONNECT_RETRIES", "3")

    device = {
        "device_name": "HQ-R1",
        "device_type": "cisco_ios",
        "model": "IOSv (EVE-NG)",
        "role": "总部核心路由器",
        "mgmt_ip": "192.0.2.1",
    }
    results = collector_ssh.collect_device(device, "admin", "wrong")

    assert len(calls) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["error_code"] == "ssh_connection_failed"
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_collector_commands.py -q
```

Expected: 第一次连接超时的用例失败（当前不重试）。

- [ ] **Step 3: 实现重试**

- `_connect_attempts()` 读取 `SSH_CONNECT_RETRIES`，夹在 0–5，返回 `retries + 1`；
- `_connect_with_retry()` 循环调用 `ConnectHandler`；
- `NetmikoAuthenticationException` 立即抛出；
- 其他异常在还有尝试次数时记录 warning 并休眠；
- 最后一次仍失败则抛出最后一次异常。

`collect_device()` 和 `execute_ssh_command()` 都改用该函数。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_collector_commands.py -q
```

Expected: PASS。

- [ ] **Step 5: 真实 HQ-R1 采集回归**

使用临时库采集 HQ-R1，确认 6/6 命令成功且没有 `show vlan brief`。

- [ ] **Step 6: 全量回归**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

- [ ] **Step 7: Commit**

```bash
git add collector/collector_ssh.py tests/test_collector_commands.py docs/superpowers/specs/2026-10-26-ssh-connection-retry-design.md docs/superpowers/plans/2026-10-26-ssh-connection-retry.md
git commit -m "fix(collector): retry transient ssh connection failures"
```
