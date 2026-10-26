# AI 诊断链路鲁棒性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复真实多设备诊断因证据过多导致的模型输出截断，并让设备识别、Prompt、LLM 配置和失败状态可验证。

**Architecture:** 在现有 `rag_engine` 边界内增量修改：证据构造增加全局/单设备上限和故障优先级；设备提取增加名称与 IP 精确匹配；Prompt 限制引用与命令格式；LLM 客户端读取环境配置并暴露 `finish_reason`；诊断引擎在截断或解析失败时用精简证据重试一次，仍失败则标记 `model_status=error`。不新增数据库迁移。

**Tech Stack:** Python 3.12、pytest、OpenAI 兼容 SDK、FastAPI、SQLite、React（仅回归）。

**Spec:** `docs/superpowers/specs/2026-10-26-ai-diagnosis-robustness-design.md`

## Global Constraints

- 保持 `/api/v1` 和现有 DTO 兼容；新增返回字段只增不改。
- 不修改已发布数据库迁移；本次不新增表/列。
- 所有新行为先写失败测试，再实现。
- 默认证据上限 30 条、单设备 12 条、重试证据 8 条。
- 默认 `LLM_MAX_TOKENS=3000`，仍允许环境变量覆盖。
- 真实 LLM/设备验收使用临时 SQLite 库，不污染 `data/ops.db`。

## Review Focus

- 证据超过上限时，不能只保留第一台设备；每台有数据的设备至少保留一条。测试在 Task 1。
- 证据中 down/admin-down 接口必须优先于普通 up 接口。测试在 Task 1。
- 问题中出现 `BR-A-R2` 或 `192.168.249.11` 时，不能再返回全部设备。测试在 Task 2。
- 模型返回 `finish_reason=length` 时必须用更少证据重试，不能直接把截断 JSON 落库。测试在 Task 4。
- 两次都失败时必须 `model_status=error`，不能继续写成成功结论。测试在 Task 4。
- Prompt 必须要求单条命令，避免模型输出多行配置被当成可执行命令。测试在 Task 3。

---

### Task 1: 证据上限与故障优先级

**Files:**
- Modify: `rag_engine/evidence_builder.py`
- Create: `tests/test_evidence_selection.py`

**Interfaces:**
- Consumes: `build_evidence_for_devices(device_names: list[str]) -> list[dict]`。
- Produces:
  - `MAX_EVIDENCE_ITEMS: int = 30`
  - `MAX_EVIDENCE_PER_DEVICE: int = 12`
  - `select_compact_evidence(evidence: list[dict], limit: int = MAX_EVIDENCE_ITEMS) -> list[dict]`
  - 每条证据新增 `priority: int` 字段（越小越优先）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_evidence_selection.py
from rag_engine.evidence_builder import (
    MAX_EVIDENCE_ITEMS,
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
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_evidence_selection.py -q
```

Expected: `ImportError`/`AttributeError`，因为 `select_compact_evidence` 尚不存在。

- [ ] **Step 3: 实现证据优先级和上限**

在 `rag_engine/evidence_builder.py` 增加常量、`_observation_priority`、`select_compact_evidence`；
`build_evidence_for_devices` 返回前调用 `select_compact_evidence`；`system` 摘要使用 `_short(..., 260)`。

选择规则：

1. 每台设备至少保留 1 条；
2. 每台设备最多 12 条；
3. 全局最多 30 条；
4. `failed/unreachable`、down/admin-down 接口优先级最高；
5. 普通接口、system、config、command_output 优先级最低；
6. stale 证据优先级降低。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_evidence_selection.py tests/test_evidence.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add rag_engine/evidence_builder.py tests/test_evidence_selection.py
git commit -m "fix(diagnosis): cap evidence and prioritize fault facts"
```

---

### Task 2: 设备名和管理 IP 精确识别

**Files:**
- Modify: `rag_engine/device_extractor.py`
- Modify: `tests/test_device_extractor.py`

**Interfaces:**
- Consumes: `db.list_devices() -> list[dict]`，字段 `device_name`、`mgmt_ip`、`site`。
- Produces: `extract_relevant_devices(question: str) -> list[str]`。

匹配顺序：

1. `device_name` 精确词匹配（大小写不敏感，带字母数字边界）；
2. `mgmt_ip` 精确匹配（带数字边界）；
3. 若命中设备名/IP，只返回精确命中的设备；
4. 否则按现有站点关键词匹配；
5. 仍无命中时返回全部未退役设备。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_device_extractor.py
def test_exact_device_names_do_not_expand_to_all_devices():
    names = extract_relevant_devices("BR-A-R2 和 BR-A-SW2 之间不通")
    assert set(names) == {"BR-A-R2", "BR-A-SW2"}


def test_device_name_is_case_insensitive():
    assert extract_relevant_devices("hq-r1 的 Gi0/1 状态怎么样") == ["HQ-R1"]


def test_management_ip_matches_device():
    assert extract_relevant_devices("192.168.249.11 的接口状态") == ["HQ-R1"]
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_device_extractor.py -q
```

Expected: 精确设备测试失败，当前会返回全部设备或整个站点。

- [ ] **Step 3: 实现精确匹配**

用 `re` 构造字母数字边界正则，避免 `HQ-R1` 匹配到 `XHQ-R1Y`；IP 使用数字边界。
保留现有站点关键词和“未知站点返回全部设备”的行为。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_device_extractor.py -q
```

Expected: 现有 2 个测试 + 新增 3 个测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add rag_engine/device_extractor.py tests/test_device_extractor.py
git commit -m "fix(diagnosis): match devices by name and management ip"
```

---

### Task 3: Prompt 限制证据引用和输出长度

**Files:**
- Modify: `rag_engine/prompt_templates.py`
- Modify: `tests/test_suggestion_dto.py`

**Interfaces:**
- Produces: `DIAGNOSIS_SYSTEM_PROMPT` 中明确：
  - `evidence_used` 最多 5 条；
  - `check_steps` 最多 6 条；
  - `fix_suggestions` 最多 4 条；
  - `command` 必须是单条命令，不允许换行或多行配置。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_suggestion_dto.py
def test_prompt_bounds_evidence_and_command_output():
    from rag_engine.prompt_templates import DIAGNOSIS_SYSTEM_PROMPT

    assert "最多 5 条" in DIAGNOSIS_SYSTEM_PROMPT
    assert "不要逐条复述全部证据" in DIAGNOSIS_SYSTEM_PROMPT
    assert "单条命令" in DIAGNOSIS_SYSTEM_PROMPT
    assert "不要输出多行" in DIAGNOSIS_SYSTEM_PROMPT
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_suggestion_dto.py -q
```

Expected: 新断言失败。

- [ ] **Step 3: 修改 System Prompt**

保留现有安全规则和 JSON 结构；修改规则 1，加入“只填关键证据、最多 5 条、不要逐条复述”；
增加输出长度和单条命令约束。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_suggestion_dto.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add rag_engine/prompt_templates.py tests/test_suggestion_dto.py
git commit -m "fix(diagnosis): bound evidence citations and command output"
```

---

### Task 4: LLM 配置、截断重试和错误状态

**Files:**
- Modify: `rag_engine/llm_client.py`
- Modify: `rag_engine/diagnosis_engine.py`
- Modify: `webapp/environment_settings.py`
- Modify: `.env.example`
- Create: `tests/test_llm_config.py`
- Create: `tests/test_diagnosis_retry.py`

**Interfaces:**
- `llm_client.py`:
  - `get_temperature() -> float`（`LLM_TEMPERATURE`，默认 0.2）
  - `get_max_tokens() -> int`（`LLM_MAX_TOKENS`，默认 3000）
  - `get_timeout() -> float`（`LLM_TIMEOUT`，默认 60）
  - `chat_completion(messages, temperature=None, max_tokens=None, response_format_json=True)`
  - 返回的 `params_used` 增加 `_finish_reason`。
- `diagnosis_engine.diagnose()` 返回增加：
  - `model_status: str`
  - `model_error_code: str | None`
  - `model_error_message: str | None`
- 常量：
  - `RETRY_EVIDENCE_ITEMS = 8`
  - `RETRY_MAX_TOKENS = 3000`

- [ ] **Step 1: 写 LLM 配置失败测试**

```python
# tests/test_llm_config.py
from types import SimpleNamespace

import rag_engine.llm_client as llm


def test_chat_completion_uses_env_config(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "2345")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
    monkeypatch.setenv("LLM_TIMEOUT", "12")
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["create"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"ok": true}'),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                id="req_test",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "_client", None)
    _, _, meta = llm.chat_completion([{"role": "user", "content": "hi"}])
    assert captured["init"]["timeout"] == 12
    assert captured["create"]["max_tokens"] == 2345
    assert captured["create"]["temperature"] == 0.7
    assert meta["_finish_reason"] == "stop"


def test_invalid_max_tokens_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")
    assert llm.get_max_tokens() == llm.DEFAULT_MAX_TOKENS
```

- [ ] **Step 2: 写诊断重试失败测试**

```python
# tests/test_diagnosis_retry.py
import json

import rag_engine.diagnosis_engine as engine


def _evidence(count=20):
    return [
        {
            "device_name": "HQ-R1",
            "collected_at": "2026-10-26T00:00:00+00:00",
            "status": "success",
            "command": "show ip interface brief",
            "summary": f"接口 down {i}",
            "observation_id": f"obs_{i}",
            "run_id": "run_1",
            "state_type": "interface",
            "priority": 0 if i < 3 else 6,
        }
        for i in range(count)
    ]


def _valid_answer():
    return {
        "fault_phenomenon": "链路 down",
        "evidence_used": [{"observation_id": "obs_0", "device_name": "HQ-R1",
                           "collected_at": "2026-10-26T00:00:00+00:00", "finding": "down"}],
        "possible_causes": [{"cause": "链路故障", "kb_refs": [], "confidence": "high"}],
        "check_steps": ["show interfaces"],
        "fix_suggestions": [],
        "recheck_method": "ping",
        "missing_information": "无",
    }


def test_truncated_output_retries_with_compact_evidence(monkeypatch):
    calls = []

    def fake_chat(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return '{"fault_phenomenon": "截断', 10, {
                "model": "fake", "_finish_reason": "length", "_status": "success",
            }
        return json.dumps(_valid_answer(), ensure_ascii=False), 20, {
            "model": "fake", "_finish_reason": "stop", "_status": "success",
        }

    monkeypatch.setattr(engine, "chat_completion", fake_chat)
    monkeypatch.setattr(engine, "extract_relevant_devices", lambda q: ["HQ-R1"])
    monkeypatch.setattr(engine, "build_evidence_for_devices", lambda names: _evidence())
    monkeypatch.setattr(engine.db, "save_diagnosis_run", lambda **kwargs: {
        "diagnosis_id": 1, "session_id": "s", "conclusion_id": "c",
        "context_snapshot_id": "ctx", "retrieval_query_id": "q",
        "model_call_id": "m",
    })
    result = engine.diagnose("HQ-R1 链路正常吗", use_kb=False)
    assert len(calls) == 2
    assert result["answer"]["fault_phenomenon"] == "链路 down"
    assert result["model_status"] == "success"
    assert sum(1 for line in calls[1][-1]["content"].splitlines() if line.startswith("- 设备:")) <= 8


def test_retry_failure_marks_model_error(monkeypatch):
    saved = {}

    def fake_chat(messages, **kwargs):
        return "not json", 5, {"model": "fake", "_finish_reason": "stop", "_status": "success"}

    def fake_save(**kwargs):
        saved.update(kwargs)
        return {
            "diagnosis_id": 1, "session_id": "s", "conclusion_id": "c",
            "context_snapshot_id": "ctx", "retrieval_query_id": "q",
            "model_call_id": "m",
        }

    monkeypatch.setattr(engine, "chat_completion", fake_chat)
    monkeypatch.setattr(engine, "extract_relevant_devices", lambda q: ["HQ-R1"])
    monkeypatch.setattr(engine, "build_evidence_for_devices", lambda names: _evidence())
    monkeypatch.setattr(engine.db, "save_diagnosis_run", fake_save)
    result = engine.diagnose("HQ-R1 链路正常吗", use_kb=False)
    assert result["model_status"] == "error"
    assert saved["model_status"] == "error"
    assert saved["model_error_code"] == "LLM_OUTPUT_INVALID"
```

- [ ] **Step 3: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_llm_config.py tests/test_diagnosis_retry.py -q
```

Expected: 配置未生效、重试不存在、错误状态不存在。

- [ ] **Step 4: 实现配置读取与 finish_reason**

`llm_client.py` 增加安全解析函数；`get_client()` 传入 `timeout=get_timeout()`；
`chat_completion()` 默认使用环境温度/上限，并记录 `_finish_reason`。

`environment_settings.py` 的 `SETTING_KEYS`/`DEFAULTS` 增加 `LLM_MAX_TOKENS`；
`.env.example` 增加 `LLM_MAX_TOKENS`、`LLM_TEMPERATURE`、`LLM_TIMEOUT` 注释示例。

- [ ] **Step 5: 实现诊断重试与错误状态**

`diagnosis_engine.diagnose()`：

1. 首次调用后读取 `params_used.get("_finish_reason")`；
2. 若为 `length`，或 `safe_parse_json` 返回“模型输出格式解析失败”，
   用 `select_compact_evidence(evidence_sanitized, limit=RETRY_EVIDENCE_ITEMS)` 重建 Prompt，
   以 `retry_max_tokens=RETRY_MAX_TOKENS` 重试一次；
3. 重试成功则使用重试答案，`elapsed_ms` 累加，`params_used["_retry"]=True`；
4. 两次都失败则 `model_status="error"`、`model_error_code="LLM_OUTPUT_INVALID"`，
   `model_error_message` 包含 `finish_reason`；
5. 把 `model_status`/`model_error_code`/`model_error_message` 传入
   `db.save_diagnosis_run` 并加入返回字典。

- [ ] **Step 6: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_llm_config.py tests/test_diagnosis_retry.py tests/test_llm_parse.py -q
```

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add rag_engine/llm_client.py rag_engine/diagnosis_engine.py webapp/environment_settings.py .env.example tests/test_llm_config.py tests/test_diagnosis_retry.py
git commit -m "fix(diagnosis): honor llm config and retry truncated output"
```

---

### Task 5: 全量回归与真实环境验收

**Files:**
- No production changes expected.

- [ ] **Step 1: 后端全量测试**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q collector control rag_engine knowledge_base ops_tools webapp test_cases app_bootstrap.py project_config.py start.py
```

Expected: 全部通过。

- [ ] **Step 2: 前端回归**

```bash
cd frontend
npm test
npm run build
```

Expected: 15 个测试通过，构建成功。

- [ ] **Step 3: 真实采集 + 诊断**

使用临时数据库，采集 HQ-R1 + HQ-SW1，提问“总部核心路由器和交换机现在正常吗？”，验证：

- 证据数 ≤ 30；
- 返回合法 JSON；
- `model_status == "success"`；
- `evidence_used` ≤ 5；
- 报告可生成。

- [ ] **Step 4: 提交计划与文档**

```bash
git add docs/superpowers/specs/2026-10-26-ai-diagnosis-robustness-design.md docs/superpowers/plans/2026-10-26-ai-diagnosis-robustness.md
git commit -m "docs: plan ai diagnosis robustness work"
```
