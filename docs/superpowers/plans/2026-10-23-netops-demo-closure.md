# NetOps Console 演示闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按“诊断 → 采集 → 终端 → 验证”的纵向链路，把当前可运行的 NetOps Console 收口成可重复演示、可验收的闭环。

**Architecture:** 在现有模块化单体上增量修复：API 层增加会话报告和归一化 DTO，采集层修正 Ping 业务语义，终端层增加实际备份门槛和一次性确认令牌，React 层消费真实契约。保留旧版 `/api` 页面和现有数据库迁移，不引入调度器、消息队列或新的重型依赖。

**Tech Stack:** Python 3.12、FastAPI、SQLite、Chroma/FastEmbed、React 18 + TypeScript、Vite、Vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-22-netops-demo-closure-design.md`

## Global Constraints

- Python 3.12 x64；Node.js 18+；单进程、单用户运行。
- 保持 `/api/v1` 向后兼容；旧版 `/api` 和 Jinja 页面继续可用。
- 数据库结构变更只能新增迁移，不能改写已发布迁移。
- 时间继续使用 UTC ISO 8601；浏览器负责本地化展示。
- 本次不实现自动调度器；页面必须真实显示“计划配置已保存，调度器未启用”。
- 不重写历史采集证据；新语义只保证新记录和新的读取结果正确。
- 写命令必须满足：`backup_status=completed` + 服务端一次性确认令牌。
- 不依赖真实 LLM 或真实设备完成验收；Mock 负责 UI，Live + 种子证据负责后端链路。
- Prompt 模板升级为 `diagnosis_v2`；旧 `diagnosis_v1` 记录只读展示。
- 现有后端测试、前端单元测试、构建和 E2E 作为回归基线。

## Review Focus

- 会话没有任何诊断记录时，报告导出必须返回明确的 404，而不是 500 或空文件。测试放在 Task 2。
- 旧诊断的 `fix_suggestions` 没有 `command`/`target_device` 时，只能展示文本，快速执行必须禁用。测试放在 Task 3。
- Ping 命令执行成功但 `parsed.reachable=false` 时，设备可达性、采集摘要和连通性统计必须一致显示失败。测试放在 Task 5。
- 旧库已保存 `collection_schedule.enabled=true` 时，页面不能继续声称自动采集正在运行。测试放在 Task 6。
- 写命令在未完成备份、令牌过期、令牌重放或命令不匹配时必须被拒绝；只读命令不受影响。测试放在 Task 7。

---

### Task 1: 依赖与 CI 基线

**Files:**
- Modify: `.github/workflows/ci.yml:21-45`
- Modify: `requirements/dev.txt`
- Modify: `start.py:32-43`
- Create: `tests/test_dependency_contract.py`

**Interfaces:**
- Consumes: 现有 `collector.run_collect` 导入链。
- Produces: 干净的 `requirements/dev.txt` 环境可以导入 `collector.collector_snmp`；`start.py` 能检测 `pysnmp` 缺失。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_dependency_contract.py
import importlib.util


def test_device_collection_dependencies_are_available():
    assert importlib.util.find_spec("pysnmp") is not None
    assert importlib.util.find_spec("netmiko") is not None
```

- [ ] **Step 2: 在隔离环境中确认失败**

```bash
python3.12 -m venv /tmp/netops-ci-check
/tmp/netops-ci-check/bin/pip install -r requirements/dev.txt
/tmp/netops-ci-check/bin/python -m pytest tests/test_dependency_contract.py -v
```

Expected: `pysnmp` 为 `None`，测试失败。

- [ ] **Step 3: 补齐依赖契约**

在 `requirements/dev.txt` 增加：

```text
-r collect.txt
```

在 `start.py` 的 `RUNTIME_MODULES` 中加入 `"pysnmp"`。

如果 CI 不使用 `dev.txt` 的传递依赖，则同时修改 `.github/workflows/ci.yml` 的 test job：

```yaml
- run: pip install -r requirements/dev.txt -r requirements/collect.txt
```

- [ ] **Step 4: 重新验证**

```bash
/tmp/netops-ci-check/bin/pip install -r requirements/dev.txt
/tmp/netops-ci-check/bin/python -m pytest tests/test_dependency_contract.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml requirements/dev.txt start.py tests/test_dependency_contract.py
git commit -m "fix(ci): install device collection dependencies for tests"
```

---

### Task 2: 按会话导出诊断报告

**Files:**
- Modify: `webapp/api/v1/router.py`（导入 `FileResponse`、`export_diagnosis_report`，新增路由）
- Modify: `frontend/src/features/diagnosis/DiagnosisPage.tsx:44-55`
- Modify: `frontend/src/features/diagnosis/AiWorkspace.tsx:345-357`
- Modify: `frontend/src/shared/api/client.ts`
- Create: `tests/test_report_export_api.py`

**Interfaces:**
- Consumes: `webapp.report_export.export_diagnosis_report(diagnosis_id: int) -> str | None`；`db.get_conn()`。
- Produces: `export_session_report(session_id: str) -> FileResponse`；`GET /api/v1/sessions/{session_id}/report`；`api.sessionReportUrl(sessionId: string): string`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_report_export_api.py
from pathlib import Path

import pytest

from collector import db
from webapp.api.v1.router import ApiProblem, export_session_report
from webapp import report_export


def _seed_diagnosis(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setattr(report_export, "REPORT_DIR", str(tmp_path / "reports"))
    db.init_db()
    db.upsert_device(
        {
            "device_name": "HQ-R1",
            "site": "总部",
            "device_type": "cisco_ios",
            "mgmt_ip": "10.0.0.1",
        }
    )
    return db.save_diagnosis_run(
        question="链路是否正常",
        model_id="test-model",
        prompt_template_id="diagnosis_v2",
        prompt_version=None,
        llm_params={},
        answer={"fault_phenomenon": "测试现象", "possible_causes": []},
        evidence=[
            {
                "device_name": "HQ-R1",
                "run_id": "run_test",
                "collected_at": "2026-10-23T00:00:00+00:00",
                "status": "success",
                "summary": "接口 up",
            }
        ],
        retrieved_docs=[],
        use_kb=False,
        kb_status="disabled",
        kb_error=None,
        response_time_ms=1,
        total_response_time_ms=1,
        context_messages=[],
        session_id=None,
    )


def test_export_session_report_returns_latest_diagnosis(tmp_path, monkeypatch):
    saved = _seed_diagnosis(tmp_path, monkeypatch)
    response = export_session_report(saved["session_id"])
    assert Path(response.path).name == f"diagnosis_{saved['diagnosis_id']}.md"
    assert "故障诊断报告" in Path(response.path).read_text(encoding="utf-8")


def test_export_session_report_unknown_session_returns_404(tmp_path, monkeypatch):
    _seed_diagnosis(tmp_path, monkeypatch)
    with pytest.raises(ApiProblem) as exc:
        export_session_report("session_missing")
    assert exc.value.status == 404
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_report_export_api.py -v
```

Expected: `ImportError` 或 `export_session_report` 未定义。

- [ ] **Step 3: 实现后端路由**

在 `webapp/api/v1/router.py` 增加：

```python
from fastapi.responses import FileResponse
from webapp.report_export import export_diagnosis_report
import os


def _latest_diagnosis_id_for_session(session_id: str) -> int | None:
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM diagnosis_records
               WHERE session_id=? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
    return int(row["id"]) if row else None


@router.get("/sessions/{session_id}/report")
def export_session_report(session_id: str):
    diagnosis_id = _latest_diagnosis_id_for_session(session_id)
    if diagnosis_id is None:
        raise ApiProblem(404, "SESSION_REPORT_NOT_FOUND", "该会话还没有可导出的诊断记录")
    path = export_diagnosis_report(diagnosis_id)
    if not path:
        raise ApiProblem(404, "DIAGNOSIS_NOT_FOUND", "诊断记录不存在")
    db.record_audit_event(
        actor="web",
        action="report.export",
        target_type="session",
        target_id=session_id,
        details={"diagnosis_id": diagnosis_id, "path": path},
    )
    return FileResponse(
        path,
        filename=os.path.basename(path),
        media_type="text/markdown",
    )
```

- [ ] **Step 4: 增加前端 URL 和入口**

在 `frontend/src/shared/api/client.ts` 的 `api` 对象中增加：

```ts
sessionReportUrl(sessionId: string): string {
  return `/api/v1/sessions/${encodeURIComponent(sessionId)}/report`;
},
```

在 `DiagnosisPage.tsx` 将顶部按钮替换为链接：

```tsx
<a
  className="button button-quiet"
  href={session.data ? api.sessionReportUrl(session.data.id) : undefined}
  target="_blank"
  rel="noreferrer"
  aria-disabled={!session.data}
>
  <FileDown size={15} />
  导出报告
</a>
```

在 `AiWorkspace.tsx` 的会话条中替换旧链接：

```tsx
<a href={api.sessionReportUrl(session.id)} target="_blank" rel="noreferrer">
  <Download size={13} />
  导出报告
</a>
```

- [ ] **Step 5: 运行测试和构建**

```bash
.venv/bin/python -m pytest tests/test_report_export_api.py -v
cd frontend && npm test -- --run && npm run build
```

Expected: 后端报告测试 PASS；前端测试和构建 PASS。

- [ ] **Step 6: Commit**

```bash
git add webapp/api/v1/router.py frontend/src/features/diagnosis/DiagnosisPage.tsx \
  frontend/src/features/diagnosis/AiWorkspace.tsx frontend/src/shared/api/client.ts \
  tests/test_report_export_api.py
git commit -m "feat(diagnosis): export reports by session"
```

---

### Task 3: 归一化诊断建议 DTO 与 Prompt 契约

**Files:**
- Modify: `rag_engine/prompt_templates.py:6-38`
- Modify: `webapp/api/v1/router.py`（新增 `_suggestion_dto`，替换 `_session_detail_dto` 的建议块）
- Modify: `frontend/src/shared/types.ts:178-184`
- Modify: `frontend/src/features/diagnosis/AiWorkspace.tsx:444-475, 569-603`
- Modify: `frontend/src/shared/mocks/data.ts:330-339`
- Create: `tests/test_suggestion_dto.py`

**Interfaces:**
- Consumes: `answer["fix_suggestions"]`、`answer["check_steps"]`、`evidence` 列表。
- Produces: `_suggestion_dto(answer: dict, evidence: list[dict]) -> dict | None`，字段为 `title`、`actions`、`command`、`targetDevice`、`canExecute`、`note`、`requiresManualConfirmation`。
- Produces: `DIAGNOSIS_TEMPLATE_ID = "diagnosis_v2"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_suggestion_dto.py
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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_suggestion_dto.py -v
```

Expected: 导入失败或字段不存在。

- [ ] **Step 3: 实现 DTO**

在 `webapp/api/v1/router.py` 增加：

```python
def _suggestion_dto(answer: dict, evidence: list[dict]) -> dict | None:
    raw = answer.get("fix_suggestions") or []
    actions: list[str] = []
    commands: list[str] = []
    target_device: str | None = None
    for item in raw:
        if isinstance(item, str):
            actions.append(item)
            continue
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        command = str(item.get("command") or "").strip()
        if action:
            actions.append(action)
        if command:
            commands.append(command)
        if not target_device and item.get("target_device"):
            target_device = str(item["target_device"])
    check_steps = [str(step) for step in (answer.get("check_steps") or []) if str(step).strip()]
    if not actions and check_steps:
        actions = check_steps
    if not target_device:
        devices = {
            str(e.get("device_name"))
            for e in evidence
            if isinstance(e, dict) and e.get("device_name")
        }
        if len(devices) == 1:
            target_device = next(iter(devices))
    if not actions and not commands:
        return None
    return {
        "title": actions[0] if actions else "按证据执行检查或修复步骤",
        "actions": actions,
        "command": commands,
        "targetDevice": target_device,
        "canExecute": bool(commands and target_device),
        "note": "模型仅提供建议。执行前请确认配置备份，并由人工决定是否下发。",
        "requiresManualConfirmation": True,
    }
```

将 `_session_detail_dto` 的 `suggestion` 块替换为：

```python
suggestion = _suggestion_dto(answer, evidence)
```

并在返回字典中使用 `"suggestion": suggestion`。

- [ ] **Step 4: 更新 Prompt v2**

在 `rag_engine/prompt_templates.py`：

```python
DIAGNOSIS_TEMPLATE_ID = "diagnosis_v2"
```

将 `fix_suggestions` 的 JSON 结构改为：

```json
"fix_suggestions": [
  {
    "action": "人类可读的处理建议",
    "command": "可选：单条可执行命令，没有则填 null",
    "target_device": "可选：目标设备名，无法确定则填 null",
    "requires_manual_confirmation": true,
    "kb_refs": ["KB-xxx-001"]
  }
]
```

- [ ] **Step 5: 更新前端类型和渲染**

`frontend/src/shared/types.ts`：

```ts
suggestion: {
  title: string;
  actions: string[];
  command: string[];
  targetDevice: string | null;
  canExecute: boolean;
  note: string;
  requiresManualConfirmation: boolean;
} | null;
```

`AiWorkspace.tsx`：

- 删除 `const suggestionTarget = ...`。
- 用 `suggestion.targetDevice` 作为终端目标。
- 在建议卡片中渲染 `suggestion.actions` 列表，再渲染 `suggestion.command` 的代码块。
- “确认后快速执行”按钮仅在 `suggestion.canExecute` 为 true 时显示或启用；`targetDevice` 为空时不允许快速执行。
- `queueTerminalCommands` 只在 `targetDevice` 和 `command` 都有效时调用。

- [ ] **Step 6: 更新 Mock 数据**

`frontend/src/shared/mocks/data.ts` 的 `mockSession.suggestion` 增加：

```ts
targetDevice: "BR-A-R2",
actions: ["检查 BR-A-R2 Gi0/1 与 BR-A-SW2 Gi0/0 链路"],
canExecute: true,
```

保留原有 `command` 字符串数组。

- [ ] **Step 7: 验证**

```bash
.venv/bin/python -m pytest tests/test_suggestion_dto.py tests/test_db_phase2.py -v
cd frontend && npm test -- --run
```

Expected: 全部 PASS。

- [ ] **Step 8: Commit**

```bash
git add rag_engine/prompt_templates.py webapp/api/v1/router.py \
  frontend/src/shared/types.ts frontend/src/features/diagnosis/AiWorkspace.tsx \
  frontend/src/shared/mocks/data.ts tests/test_suggestion_dto.py
git commit -m "feat(diagnosis): normalize suggestions and add executable commands"
```

---

### Task 4: 会话刷新、历史入口和工具设备名

**Files:**
- Modify: `webapp/api/v1/router.py`（`tool_calls` DTO、`list_sessions` 已存在）
- Modify: `frontend/src/shared/types.ts`
- Modify: `frontend/src/shared/api/client.ts`
- Modify: `frontend/src/features/diagnosis/AiWorkspace.tsx`
- Modify: `frontend/src/features/diagnosis/DiagnosisPage.tsx`
- Create: `tests/test_session_tool_dto.py`

**Interfaces:**
- Consumes: `GET /api/v1/sessions`、`GET /api/v1/sessions/{id}`、`db.start_tool_call` / `db.finish_tool_call`。
- Produces: `toolCalls[].deviceName: string | null`；`api.listSessions(limit?: number): Promise<DiagnosisSession[]>`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_session_tool_dto.py
from collector import db
from webapp.api.v1.router import _session_detail_dto


def test_tool_call_dto_exposes_device_name(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    saved = db.save_diagnosis_run(
        question="检查 HQ-R1",
        model_id="test",
        prompt_template_id="diagnosis_v2",
        prompt_version=None,
        llm_params={},
        answer={"fault_phenomenon": "测试", "possible_causes": []},
        evidence=[],
        retrieved_docs=[],
        use_kb=False,
        kb_status="disabled",
        kb_error=None,
        response_time_ms=1,
        total_response_time_ms=1,
        context_messages=[],
        session_id=None,
    )
    call = db.start_tool_call(
        "query_device_state",
        {"device_names": ["HQ-R1"]},
        session_id=saved["session_id"],
    )
    db.finish_tool_call(call["tool_call_id"], status="success", result={"ok": True})
    dto = _session_detail_dto(saved["session_id"])
    assert dto["toolCalls"][0]["deviceName"] == "HQ-R1"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_session_tool_dto.py -v
```

Expected: `KeyError: 'deviceName'`。

- [ ] **Step 3: 后端补字段**

在 `_session_detail_dto` 的 tool call 循环中解析参数：

```python
arguments = db._json_value(call.get("arguments_json")) or {}
device_name = arguments.get("device_name")
if not device_name:
    names = arguments.get("device_names") or []
    device_name = names[0] if names else None
```

在 `tool_calls.append({...})` 中加入：

```python
"deviceName": device_name,
```

- [ ] **Step 4: 前端增加会话列表与导航刷新**

`frontend/src/shared/api/client.ts` 增加：

```ts
async listSessions(limit = 20): Promise<DiagnosisSession[]> {
  if (mockMode) return delay([mockSession]);
  return request(`/api/v1/sessions?limit=${limit}`);
},
```

`frontend/src/shared/types.ts` 的 `toolCalls` 项增加：

```ts
deviceName: string | null;
```

`AiWorkspace.tsx`：

- 使用 `useNavigate`。
- 提交诊断成功后：

```ts
navigate(`/diagnosis?session=${encodeURIComponent(result.session_id)}`, { replace: true });
```

- 用 `useQuery` 加载 `api.listSessions()`，在会话面板中渲染历史会话按钮，点击后导航到对应 `?session=`。
- 工具调用按钮使用 `call.deviceName`；为 null 时按钮禁用。
- `startNewSession` 使用 `navigate("/diagnosis", { replace: true })`，并清空本地状态。
- 将“网络搜索”按钮设为 `disabled`，标题说明“生产阶段接入”，避免暗示当前可用。

- [ ] **Step 5: 验证**

```bash
.venv/bin/python -m pytest tests/test_session_tool_dto.py -v
cd frontend && npm test -- --run && npm run build
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add webapp/api/v1/router.py frontend/src/shared/types.ts \
  frontend/src/shared/api/client.ts frontend/src/features/diagnosis/AiWorkspace.tsx \
  frontend/src/features/diagnosis/DiagnosisPage.tsx tests/test_session_tool_dto.py
git commit -m "feat(diagnosis): refresh sessions and expose tool device targets"
```

---

### Task 5: 修正 Ping 业务可达性语义

**Files:**
- Modify: `collector/connectivity_test.py:60-134`
- Modify: `collector/run_collect.py:59-80, 207-244`
- Create: `tests/test_connectivity_semantics.py`

**Interfaces:**
- Consumes: `collect_terminal_ping` 返回的 `parsed.reachable`。
- Produces: `collect_terminal_ping` 顶层 `reachable: bool | None`；`run_collect._result_ok(result: dict) -> bool`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_connectivity_semantics.py
from collector import db, run_collect


def test_ping_command_success_but_unreachable_is_not_business_success(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()
    db.upsert_device(
        {
            "device_name": "HQ-SRV",
            "site": "总部",
            "device_type": "linux",
            "mgmt_ip": "10.10.10.100",
        }
    )
    db.upsert_device(
        {
            "device_name": "BR-A-PC",
            "site": "分支A",
            "device_type": "linux",
            "mgmt_ip": "10.20.10.100",
        }
    )
    monkeypatch.setenv("DEVICE_USERNAME", "test")
    monkeypatch.setenv("DEVICE_PASSWORD", "test")
    monkeypatch.setattr(
        run_collect,
        "collect_terminal_ping",
        lambda device, target, username, password: {
            "status": "success",
            "source": "ping",
            "capture_type": "ping",
            "command": f"ping -c 4 -W 2 {target}",
            "started_at": "2026-10-23T00:00:00+00:00",
            "finished_at": "2026-10-23T00:00:01+00:00",
            "raw_output": "4 packets transmitted, 0 received, 100% packet loss",
            "parsed": {"target_ip": target, "reachable": False},
            "observation_status": "unreachable",
        },
    )

    result = run_collect.run_collection(trigger_type="manual", requested_by="test")

    assert result["connectivity_tests"] == 1
    assert result["connectivity_successes"] == 0
    assert result["connectivity_failures"] == 1
    status = {row["device_name"]: row for row in db.get_current_device_status()}
    assert status["BR-A-PC"]["reachability"] == "unreachable"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_connectivity_semantics.py -v
```

Expected: `connectivity_successes == 1` 或设备显示 `reachable`，断言失败。

- [ ] **Step 3: 修正采集结果**

在 `collector/connectivity_test.py` 的 `collect_terminal_ping` 返回中增加顶层字段：

```python
"reachable": reachable,
"observation_status": "observed" if reachable else "unreachable",
```

失败路径保持 `reachable: None`。

- [ ] **Step 4: 统一业务成功判断**

在 `collector/run_collect.py` 增加：

```python
def _result_ok(result: dict) -> bool:
    if result.get("status") != "success":
        return False
    parsed = result.get("parsed") or {}
    if result.get("capture_type") == "ping" and parsed.get("reachable") is False:
        return False
    return True
```

将 `_device_overall` 中的 `_status_counts` 改为使用 `_result_ok`，并在 `run_collection` 中：

```python
if _result_ok(ping_result):
    connectivity_successes += 1
else:
    connectivity_failures += 1
```

`total_success` / `total_failed` 也改用 `_result_ok`。

- [ ] **Step 5: 验证**

```bash
.venv/bin/python -m pytest tests/test_connectivity_semantics.py tests/test_collection_run.py -v
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add collector/connectivity_test.py collector/run_collect.py tests/test_connectivity_semantics.py
git commit -m "fix(collector): treat unreachable ping as failed connectivity"
```

---

### Task 6: 采集计划的真实状态与手动任务进度

**Files:**
- Modify: `webapp/api/v1/router.py:685-718`
- Modify: `frontend/src/shared/types.ts:53-62`
- Modify: `frontend/src/features/assets/AssetsPage.tsx`
- Create: `frontend/src/features/assets/scheduleLabel.ts`
- Create: `frontend/src/features/assets/scheduleLabel.test.ts`
- Modify: `tests/test_api_v1.py`

**Interfaces:**
- Consumes: `db.get_application_setting`、`api.triggerCollection`、`api.getJob`。
- Produces: `GET/PUT /api/v1/collection-schedule` 返回 `scheduler_available: false`；`CollectionSchedule.scheduler_available: boolean`；`scheduleLabel(schedule) -> { title: string; detail: string; active: boolean }`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_api_v1.py 追加
def test_collection_schedule_does_not_claim_scheduler(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    data = api_v1.get_collection_schedule()
    assert data["enabled"] is False
    assert data["scheduler_available"] is False

    updated = api_v1.update_collection_schedule(
        api_v1.ScheduleUpdate(enabled=True, frequency_minutes=30)
    )
    assert updated["scheduler_available"] is False
```

```ts
// frontend/src/features/assets/scheduleLabel.test.ts
import { describe, expect, it } from "vitest";
import { scheduleLabel } from "./scheduleLabel";

describe("scheduleLabel", () => {
  it("does not claim auto collection when scheduler is unavailable", () => {
    const label = scheduleLabel({
      enabled: true,
      scheduler_available: false,
      frequency_minutes: 30,
      active_window: { start: "00:00", end: "23:59" },
      scope: "all",
      concurrency: 4,
      timeout_seconds: 15,
      retries: 1,
      channels: {}
    });
    expect(label.active).toBe(false);
    expect(label.title).toContain("未启用");
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_api_v1.py::test_collection_schedule_does_not_claim_scheduler -v
cd frontend && npm test -- --run src/features/assets/scheduleLabel.test.ts
```

Expected: 后端缺少 `scheduler_available`；前端模块不存在。

- [ ] **Step 3: 后端返回真实状态**

将 `DEFAULT_SCHEDULE["enabled"]` 改为 `False`，并增加：

```python
SCHEDULER_AVAILABLE = False


def _schedule_response(value: dict) -> dict:
    return {**value, "scheduler_available": SCHEDULER_AVAILABLE}
```

`get_collection_schedule` 和 `update_collection_schedule` 均返回 `_schedule_response(...)`。

- [ ] **Step 4: 前端标签和类型**

`frontend/src/shared/types.ts` 增加：

```ts
scheduler_available: boolean;
```

创建 `scheduleLabel.ts`：

```ts
import type { CollectionSchedule } from "../../shared/types";

export function scheduleLabel(schedule: CollectionSchedule) {
  if (!schedule.scheduler_available) {
    return {
      active: false,
      title: "自动采集调度未启用",
      detail: "计划配置已保存，当前需要手动采集"
    };
  }
  return {
    active: schedule.enabled,
    title: schedule.enabled ? "自动采集已开启" : "自动采集已关闭",
    detail: `每 ${schedule.frequency_minutes} 分钟`
  };
}
```

`AssetsPage.tsx` 的初始 `scheduleForm` 增加 `scheduler_available: false`，并使用 `scheduleLabel(scheduleForm)` 替换硬编码横幅文案；调度器不可用时将“启用自动采集”复选框设为 `disabled`。

- [ ] **Step 5: 增加手动任务进度**

在 `AssetsPage` 中增加：

```ts
const [collectionJobId, setCollectionJobId] = useState<string | null>(null);
const collectionJob = useQuery({
  queryKey: ["collection-job", collectionJobId],
  queryFn: () => api.getJob(collectionJobId!),
  enabled: Boolean(collectionJobId),
  refetchInterval: (query) => {
    const status = query.state.data?.status;
    return status && ["success", "partial", "failed", "cancelled"].includes(status) ? false : 1000;
  }
});
```

`useEffect` 监听 `collectionJob.data?.status`：终态时刷新 `collection-runs` 和 `overview`，设置提示文案并清空 `collectionJobId`。

将“立即采集”按钮改为：

```ts
const created = await api.triggerCollection();
setCollectionJobId(created.jobId);
setActionMessage("采集任务已创建，正在执行…");
```

在横幅或提示区显示 `collectionJob.data.progress`、`status` 和 `message`。

- [ ] **Step 6: 验证**

```bash
.venv/bin/python -m pytest tests/test_api_v1.py -v
cd frontend && npm test -- --run && npm run build
```

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add webapp/api/v1/router.py frontend/src/shared/types.ts \
  frontend/src/features/assets/AssetsPage.tsx frontend/src/features/assets/scheduleLabel.ts \
  frontend/src/features/assets/scheduleLabel.test.ts tests/test_api_v1.py
git commit -m "fix(collection): make schedule truthful and show job progress"
```

---

### Task 7: 终端写命令确认与备份门槛（后端）

**Files:**
- Create: `control/write_policy.py`
- Create: `control/terminal_confirmations.py`
- Modify: `control/terminal_service.py`
- Modify: `webapp/terminal_api.py`
- Create: `tests/test_write_policy.py`
- Create: `tests/test_write_confirmations.py`
- Modify: `tests/test_api_v1.py`

**Interfaces:**
- Consumes: `terminal_sessions.backup_status`、`db.get_terminal_session`、`db.get_device`。
- Produces: `is_write_command(device_type: str, command: str) -> bool`；`WriteConfirmationStore.issue(session_id, command) -> dict`；`WriteConfirmationStore.consume(session_id, command, confirmation_id) -> bool`；`TerminalManager.request_write_confirmation(session_id, command) -> dict`；`TerminalManager.execute(..., confirmation_id: str | None = None)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_write_policy.py
from control.write_policy import is_write_command


def test_common_write_commands_are_detected():
    for command in (
        "configure terminal",
        "interface GigabitEthernet0/1",
        "no shutdown",
        "ip route 0.0.0.0 0.0.0.0 10.0.0.1",
        "write memory",
        "undo shutdown",
        "system-view",
    ):
        assert is_write_command("cisco_ios", command) is True


def test_read_only_commands_are_not_write():
    for command in ("show ip interface brief", "display version", "ping 10.0.0.1"):
        assert is_write_command("cisco_ios", command) is False
```

```python
# tests/test_write_confirmations.py
import control.terminal_confirmations as confirmations


def test_confirmation_is_bound_to_session_and_command(monkeypatch):
    store = confirmations.WriteConfirmationStore(ttl_seconds=30)
    issued = store.issue("term_1", "configure terminal")
    assert store.consume("term_1", "configure terminal", issued["confirmation_id"]) is True
    assert store.consume("term_1", "configure terminal", issued["confirmation_id"]) is False
    assert store.consume("term_2", "configure terminal", issued["confirmation_id"]) is False
    assert store.consume("term_1", "shutdown", issued["confirmation_id"]) is False


def test_expired_confirmation_is_rejected(monkeypatch):
    store = confirmations.WriteConfirmationStore(ttl_seconds=1)
    now = 1000.0
    monkeypatch.setattr(confirmations.time, "monotonic", lambda: now)
    issued = store.issue("term_1", "configure terminal")
    monkeypatch.setattr(confirmations.time, "monotonic", lambda: now + 2)
    assert store.consume("term_1", "configure terminal", issued["confirmation_id"]) is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_write_policy.py tests/test_write_confirmations.py -v
```

Expected: 模块不存在。

- [ ] **Step 3: 实现写命令策略**

`control/write_policy.py`：

```python
WRITE_PREFIXES = (
    "configure", "conf t", "interface ", "no shutdown", "shutdown",
    "write memory", "copy run", "vlan ", "switchport ", "ip route ",
    "access-list ", "router ", "erase ", "delete ", "reload",
    "undo ", "system-view", "commit", "save",
)


def is_write_command(device_type: str, command: str) -> bool:
    text = (command or "").strip().lower()
    return any(text.startswith(prefix) for prefix in WRITE_PREFIXES)
```

`device_type` 参数保留用于未来按平台扩展；当前所有平台共用前缀表。

- [ ] **Step 4: 实现一次性确认存储**

`control/terminal_confirmations.py`：

```python
import hashlib
import secrets
import time
import threading


class WriteConfirmationStore:
    def __init__(self, ttl_seconds: int = 120):
        self._ttl = ttl_seconds
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _hash(self, command: str) -> str:
        return hashlib.sha256((command or "").strip().encode("utf-8")).hexdigest()

    def issue(self, session_id: str, command: str) -> dict:
        token = f"wc_{secrets.token_urlsafe(24)}"
        issued_at = time.monotonic()
        with self._lock:
            self._items[token] = {
                "session_id": session_id,
                "command_hash": self._hash(command),
                "expires_at": issued_at + self._ttl,
            }
        return {
            "confirmation_id": token,
            "expires_at": issued_at + self._ttl,
        }

    def consume(self, session_id: str, command: str, confirmation_id: str) -> bool:
        with self._lock:
            item = self._items.pop(confirmation_id, None)
            if not item:
                return False
            if time.monotonic() > item["expires_at"]:
                return False
            if item["session_id"] != session_id:
                return False
            return item["command_hash"] == self._hash(command)

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            for token, item in list(self._items.items()):
                if item["session_id"] == session_id:
                    self._items.pop(token, None)
```

`expires_at` 使用 `time.monotonic()` 的秒数，只在应用内部和测试中使用；API 层按需转换为绝对时间。

- [ ] **Step 5: 接入 TerminalManager**

在 `control/terminal_service.py`：

- 初始化 `self._confirmations = WriteConfirmationStore(...)`。
- 增加 `request_write_confirmation`：

```python
def request_write_confirmation(self, session_id: str, command: str) -> dict:
    session = db.get_terminal_session(session_id)
    if not session:
        raise TerminalError("TERMINAL_NOT_FOUND", "终端会话不存在")
    if session["status"] != "open":
        raise TerminalError("TERMINAL_CLOSED", "终端会话已关闭")
    if session.get("backupStatus") != "completed":
        raise TerminalError("TERMINAL_BACKUP_REQUIRED", "写命令前必须完成实际配置备份")
    device = db.get_device(session["deviceName"], include_retired=False)
    if not device:
        raise TerminalError("DEVICE_NOT_FOUND", "设备不存在或已退役")
    if not is_write_command(device.get("device_type") or "", command):
        raise TerminalError("TERMINAL_NOT_WRITE_COMMAND", "该命令不是写命令")
    return self._confirmations.issue(session_id, command)
```

- `execute` 增加参数 `confirmation_id: str | None = None`。在获取 session 后：

```python
device = db.get_device(session["deviceName"], include_retired=False)
write = is_write_command(device.get("device_type") or "", command) if device else False
if write:
    if session.get("backupStatus") != "completed":
        db.append_terminal_event(
            session_id, event_type="error", source="system",
            command_text=sanitize_text(command),
            output_text="写命令被拒绝：未完成实际配置备份",
            exit_code=1,
        )
        raise TerminalError("TERMINAL_BACKUP_REQUIRED", "写命令前必须完成实际配置备份")
    if not confirmation_id or not self._confirmations.consume(session_id, command, confirmation_id):
        db.append_terminal_event(
            session_id, event_type="error", source="system",
            command_text=sanitize_text(command),
            output_text="写命令被拒绝：确认令牌无效、过期或已使用",
            exit_code=1,
        )
        raise TerminalError("TERMINAL_CONFIRMATION_REQUIRED", "写命令需要有效的二次确认")
```

命令事件 metadata 增加 `{"confirmed": confirmed, "confirmation_used": bool(write)}`。`close` 调用 `self._confirmations.clear_session(session_id)`。

- [ ] **Step 6: 增加 API 端点**

`webapp/terminal_api.py`：

```python
class WriteIntentRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4000)


@router.post("/sessions/{session_id}/write-intents", status_code=201)
def create_write_intent(session_id: str, payload: WriteIntentRequest):
    try:
        return terminal_manager.request_write_confirmation(session_id, payload.command)
    except TerminalError as exc:
        status = 409 if exc.code in (
            "TERMINAL_BACKUP_REQUIRED", "TERMINAL_CONFIRMATION_REQUIRED"
        ) else 400
        raise ApiProblem(status, exc.code, str(exc)) from exc
```

`TerminalCommandRequest` 增加：

```python
confirmation_id: str | None = None
```

并传给 `terminal_manager.execute`。

- [ ] **Step 7: 增加 Manager/API 测试**

在 `tests/test_api_v1.py` 增加：创建会话后直接调用 `terminal_manager.request_write_confirmation` 应抛 `TERMINAL_BACKUP_REQUIRED`；调用 `db.set_terminal_backup_result(... status="completed")` 后应返回 `confirmation_id`。不需要真实 SSH 连接。

- [ ] **Step 8: 验证**

```bash
.venv/bin/python -m pytest tests/test_write_policy.py tests/test_write_confirmations.py \
  tests/test_api_v1.py -v
```

Expected: PASS。

- [ ] **Step 9: Commit**

```bash
git add control/write_policy.py control/terminal_confirmations.py \
  control/terminal_service.py webapp/terminal_api.py \
  tests/test_write_policy.py tests/test_write_confirmations.py tests/test_api_v1.py
git commit -m "feat(terminal): require backup and one-time confirmation for writes"
```

---

### Task 8: 终端写确认（前端）

**Files:**
- Modify: `frontend/src/shared/types.ts:210-234`
- Modify: `frontend/src/shared/api/client.ts:450-523`
- Modify: `frontend/src/shared/components/TerminalDock.tsx`
- Modify: `frontend/src/shared/components/TerminalDock.test.tsx`

**Interfaces:**
- Consumes: `POST /api/v1/terminal/sessions/{id}/backup`、`POST /api/v1/terminal/sessions/{id}/write-intents`、`POST /api/v1/terminal/sessions/{id}/commands`。
- Produces: `api.backupTerminal(sessionId): Promise<TerminalSession>`；`api.requestWriteConfirmation(sessionId, command): Promise<WriteConfirmation>`；`sendTerminalCommand(..., { confirmationId })`。

- [ ] **Step 1: 写失败测试**

更新 `TerminalDock.test.tsx` 的 mocks：

```ts
const mocks = vi.hoisted(() => ({
  startTerminal: vi.fn(),
  backupTerminal: vi.fn(),
  requestWriteConfirmation: vi.fn(),
  sendTerminalCommand: vi.fn(),
  closeTerminal: vi.fn()
}));
```

`startTerminal` 返回值增加 `backupStatus: "acknowledged"`；`backupTerminal` 返回 `backupStatus: "completed"`；`requestWriteConfirmation` 返回 `{ confirmation_id: "wc_test", expires_at: "..." }`。

新增断言：

```ts
it("requires a server confirmation token for write commands", async () => {
  render(<TerminalDock />);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: /建立受审计终端/ }));
  await waitFor(() => expect(mocks.startTerminal).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: "执行备份" }));
  await waitFor(() => expect(mocks.backupTerminal).toHaveBeenCalledWith("term_test"));

  fireEvent.change(screen.getByPlaceholderText(/输入命令/), {
    target: { value: "configure terminal" }
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  fireEvent.click(screen.getByRole("button", { name: "确认并下发" }));

  await waitFor(() =>
    expect(mocks.sendTerminalCommand).toHaveBeenCalledWith(
      "term_test",
      "configure terminal",
      expect.objectContaining({ confirmationId: "wc_test" })
    )
  );
});
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd frontend && npm test -- --run src/shared/components/TerminalDock.test.tsx
```

Expected: 新 mock 未定义或按钮不存在。

- [ ] **Step 3: 类型和 API client**

`frontend/src/shared/types.ts`：

```ts
export interface TerminalSession {
  id: string;
  deviceName: string;
  operator: string;
  status: "open" | "closed" | "failed";
  backupStatus: "not_requested" | "acknowledged" | "completed" | "failed";
  backupAcknowledgedAt: string;
  startedAt: string;
  endedAt: string | null;
  events: TerminalEvent[];
}

export interface WriteConfirmation {
  confirmation_id: string;
  expires_at: number;
}
```

`api` 增加：

```ts
async backupTerminal(sessionId: string): Promise<TerminalSession> {
  if (mockMode) {
    const current = await this.startTerminal("mock", true);
    return delay({ ...current, backupStatus: "completed" });
  }
  return request(`/api/v1/terminal/sessions/${sessionId}/backup`, { method: "POST" });
},

async requestWriteConfirmation(sessionId: string, command: string): Promise<WriteConfirmation> {
  if (mockMode) return delay({ confirmation_id: `wc_${Date.now()}`, expires_at: Date.now() / 1000 + 120 });
  return request(`/api/v1/terminal/sessions/${sessionId}/write-intents`, {
    method: "POST",
    body: JSON.stringify({ command })
  });
},
```

`sendTerminalCommand` 的 options 增加 `confirmationId`，请求体增加：

```ts
confirmation_id: options.confirmationId ?? null
```

mock 分支无需使用 token。

- [ ] **Step 4: TerminalDock 交互**

- `session` 状态增加 `backupStatus` 展示。
- 增加 `backupBusy` 状态和“执行备份”按钮；调用 `api.backupTerminal(session.id)`，成功后 `setSession(updated)` 并刷新事件。
- `dispatchCommand` 在发送前判断本命令是否为写命令：
  - 若 `session.backupStatus !== "completed"`，写入错误事件并返回。
  - 若是写命令，先 `api.requestWriteConfirmation(session.id, trimmed)`，再带 `confirmationId` 发送。
- 保留现有 `ConfirmDialog` 作为人工确认界面；确认后仍必须获取服务端令牌。
- 在终端头部显示 `已备份` / `未备份` 状态；未备份时写命令提示“请先执行备份”。

- [ ] **Step 5: 验证**

```bash
cd frontend && npm test -- --run src/shared/components/TerminalDock.test.tsx
cd frontend && npm run build
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/shared/types.ts frontend/src/shared/api/client.ts \
  frontend/src/shared/components/TerminalDock.tsx \
  frontend/src/shared/components/TerminalDock.test.tsx
git commit -m "feat(terminal): wire backup and server confirmation token in UI"
```

---

### Task 9: 可重复的 Live 演示种子

**Files:**
- Create: `test_cases/seed_demo.py`
- Create: `tests/test_demo_seed.py`

**Interfaces:**
- Consumes: `test_cases.scenarios.SCENARIOS`、`seed_scenario`、`db.load_inventory`、`db.save_diagnosis_run`。
- Produces: `seed_demo(db_path: str | None = None, reset: bool = False) -> dict`，返回 `{"session_id", "diagnosis_id", "run_id"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_demo_seed.py
from collector import db
from test_cases.seed_demo import seed_demo
from webapp.api.v1.router import _session_detail_dto


def test_seed_demo_creates_exportable_session(tmp_path):
    result = seed_demo(db_path=str(tmp_path / "demo.db"), reset=True)
    session = db.get_agent_session(result["session_id"])
    assert session is not None
    dto = _session_detail_dto(result["session_id"])
    assert dto["suggestion"]["canExecute"] is True
    assert dto["suggestion"]["targetDevice"] == "BR-A-R2"
    assert dto["evidence"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_demo_seed.py -v
```

Expected: `test_cases.seed_demo` 不存在。

- [ ] **Step 3: 实现种子脚本**

`test_cases/seed_demo.py`：

```python
def seed_demo(db_path: str | None = None, reset: bool = False) -> dict:
    from collector import db
    from test_cases.scenarios import SCENARIOS, seed_scenario

    old_path = db.DB_PATH
    if db_path:
        db.DB_PATH = db_path
    if reset:
        from pathlib import Path
        path = Path(db.DB_PATH)
        if path.exists():
            path.unlink()
    db.init_db()
    if not db.list_devices():
        db.load_inventory(str(PROJECT_ROOT / "network" / "device_inventory.json"))
    scenario = next(item for item in SCENARIOS if item["id"] == "LINK_DOWN_01")
    seed_scenario(scenario)
    evidence = [
        {
            "device_name": "BR-A-R2",
            "run_id": "run_demo",
            "collected_at": "2026-10-23T00:00:00+00:00",
            "status": "success",
            "summary": "Gi0/1 down/down",
        },
        {
            "device_name": "BR-A-SW2",
            "run_id": "run_demo",
            "collected_at": "2026-10-23T00:00:00+00:00",
            "status": "success",
            "summary": "Gi0/0 down/down",
        },
    ]
    saved = db.save_diagnosis_run(
        question=scenario["question"],
        model_id="demo-seed",
        prompt_template_id="diagnosis_v2",
        prompt_version=None,
        llm_params={},
        answer={
            "fault_phenomenon": "BR-A-R2 与 BR-A-SW2 互联链路 down/down",
            "possible_causes": [
                {"cause": "互联链路物理或协议异常", "confidence": "high"}
            ],
            "check_steps": ["show interfaces GigabitEthernet0/1"],
            "fix_suggestions": [
                {
                    "action": "检查互联链路并确认端口状态",
                    "command": "show interfaces GigabitEthernet0/1",
                    "target_device": "BR-A-R2",
                    "requires_manual_confirmation": True,
                }
            ],
            "recheck_method": "ping 192.168.10.100",
            "missing_information": "无",
        },
        evidence=evidence,
        retrieved_docs=[],
        use_kb=False,
        kb_status="disabled",
        kb_error=None,
        response_time_ms=1,
        total_response_time_ms=1,
        context_messages=[],
        session_id=None,
    )
    return {
        "session_id": saved["session_id"],
        "diagnosis_id": saved["diagnosis_id"],
        "run_id": "run_demo",
    }
```

用 `try/finally` 包住函数体，在 `finally` 中恢复 `db.DB_PATH = old_path`，保证测试之间不串库。CLI 运行时因为进程结束，恢复与否不影响演示库。

增加 CLI：

```python
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="./data/demo.db")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    print(seed_demo(args.db_path, reset=args.reset))
```

- [ ] **Step 4: 验证**

```bash
.venv/bin/python -m pytest tests/test_demo_seed.py tests/test_report_export_api.py -v
.venv/bin/python -m test_cases.seed_demo --db-path ./data/demo.db --reset
```

Expected: 测试 PASS，命令输出包含 `session_id` 和 `diagnosis_id`。

- [ ] **Step 5: Commit**

```bash
git add test_cases/seed_demo.py tests/test_demo_seed.py
git commit -m "test: add deterministic live demo seed"
```

---

### Task 10: E2E 修复、文档和全量验收

**Files:**
- Modify: `frontend/e2e/console.spec.ts:96-143`
- Modify: `docs/API.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/DEVELOPMENT.md`（如新增验收命令）

**Interfaces:**
- Consumes: 前九个任务的新接口和类型。
- Produces: 可重复的桌面/移动 E2E 基线；与实现一致的 API/运维文档。

- [ ] **Step 1: 修正拖动测试几何条件**

在 `frontend/e2e/console.spec.ts` 中，将设备详情浮层的拖动方向从“向下”改为“向上”，并断言 y 减小：

```ts
await page.mouse.move(detailHeader!.x + 80, detailHeader!.y + 18);
await page.mouse.down();
await page.mouse.move(detailHeader!.x + 20, detailHeader!.y - 30, { steps: 8 });
await page.mouse.up();
const detailAfter = await detail.boundingBox();
expect(detailAfter!.x).toBeLessThan(detailBefore!.x - 20);
expect(detailAfter!.y).toBeLessThan(detailBefore!.y - 10);
```

- [ ] **Step 2: 更新文档**

`docs/API.md` 增加：

```text
| GET | /api/v1/sessions/{session_id}/report | 导出该会话最近一次诊断报告 |
```

`docs/OPERATIONS.md` 更新：

- 采集计划当前只保存配置，不执行调度；默认关闭。
- 写命令要求实际备份完成和服务端一次性确认。
- 健康检查命令必须带 `Authorization: Bearer <APP_API_TOKEN>`。

`docs/ROADMAP.md`：

- 移除已完成项：报告导出、建议 DTO、Ping 语义、终端写确认。
- 保留生产硬化项：调度器、任务持久化、RBAC、部署、知识库回滚。

- [ ] **Step 3: 运行完整后端验证**

```bash
cd /home/wyj/project6
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q collector control rag_engine knowledge_base ops_tools webapp test_cases app_bootstrap.py project_config.py start.py
```

Expected: 全部 PASS。

- [ ] **Step 4: 运行前端和 E2E**

```bash
cd /home/wyj/project6/frontend
npm test
npm run build
npm run test:e2e -- --project=desktop-chrome
npm run test:e2e -- --project=mobile-chrome
```

Expected: 前端测试和构建 PASS；桌面/移动 E2E 全部通过（production-live 用例在未设置环境变量时跳过）。

- [ ] **Step 5: 验收 Live 种子演示**

```bash
cd /home/wyj/project6
.venv/bin/python -m test_cases.seed_demo --db-path ./data/demo.db --reset
SQLITE_DB_PATH=./data/demo.db .venv/bin/python start.py --live
```

在浏览器验证：

1. `/console/diagnosis?session=<seed session_id>` 显示种子证据、建议和知识区域。
2. 点击“导出报告”下载 Markdown。
3. 快速执行按钮使用建议目标设备，不再出现 `[object Object]`。
4. 设备采集横幅明确显示“调度未启用”。
5. 未备份时写命令被拒绝；备份完成后仍需确认令牌。

- [ ] **Step 6: Commit**

```bash
git add frontend/e2e/console.spec.ts docs/API.md docs/OPERATIONS.md \
  docs/ROADMAP.md docs/DEVELOPMENT.md
git commit -m "docs: align API and operations with demo closure"
```

---

## Self-Review

**Spec coverage:** 报告导出（Task 2）、建议契约（Task 3）、会话刷新/历史/工具设备（Task 4）、Ping 语义（Task 5）、计划真实状态和手动采集进度（Task 6）、终端写确认（Task 7/8）、Live 种子演示（Task 9）、CI 与文档验收（Task 1/10）。生产硬化明确留在设计文档阶段 5，不进入本次计划。

**Step scan:** 每个任务都有明确的失败测试、实现文件、验证命令和提交步骤。测试值使用具体命令、字段和断言，不保留“适当处理”类占位。

**Type consistency:** `suggestion.actions`、`suggestion.command`、`suggestion.targetDevice`、`suggestion.canExecute` 在 Task 3 定义并在 Task 4/9 使用；`TerminalSession.backupStatus`、`WriteConfirmation`、`confirmationId` 在 Task 8 统一使用；`_result_ok` 在 Task 5 定义，Task 6 不重复定义。

**Review Focus coverage:** 五个高风险输入均在对应任务中有测试：Task 2 的 404、Task 3 的旧建议、Task 5 的 Ping 不可达、Task 6 的旧计划状态、Task 7 的无效/重放/过期令牌。

**Proportion:** 计划按任务和验收点组织，不复制完整程序；实现细节留给执行者，接口、文件、测试数据和验收命令已锁定。
