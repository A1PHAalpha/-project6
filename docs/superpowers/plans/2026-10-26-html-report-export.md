# HTML 诊断报告导出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留 Markdown 导出的同时，新增可直接打印为 PDF 的 HTML 报告，并提供前端入口。

**Architecture:** 把 `report_export.py` 的报告内容构造抽成 `_build_report_markdown()`；Markdown 和 HTML 导出共用同一份内容。HTML 使用内置的轻量 Markdown 渲染器和打印样式，不引入模板引擎或新依赖。API 通过 `format` 查询参数选择格式。

**Tech Stack:** Python 3.12、FastAPI、pytest、React + TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-26-html-report-export-design.md`

## Global Constraints

- `GET /api/v1/sessions/{id}/report` 默认仍是 Markdown，旧调用不变。
- `format` 只接受 `md` 和 `html`；其他值返回 400。
- 报告内容只从现有 `_build_report_markdown` 数据构造，不新增数据源。
- HTML 必须自带打印样式，不依赖外部 CSS/JS。
- 不新增依赖，不改数据库。

## Review Focus

- 默认调用不能改变旧文件名和 `text/markdown` 媒体类型。测试在 Task 2。
- 非法 `format` 不能悄悄回退到 Markdown，必须明确 400。测试在 Task 2。
- HTML 必须转义用户问题和设备证据中的 `<`/`&`，避免注入。测试在 Task 1。
- 会话没有诊断记录时，HTML 也必须返回 404，不能生成空文件。测试在 Task 2。
- 前端默认“导出报告”仍指向 Markdown；新增“导出 HTML”不破坏旧链接。测试在 Task 2。

---

### Task 1: HTML 报告渲染

**Files:**
- Modify: `webapp/report_export.py`
- Modify: `tests/test_report_export_api.py`

**Interfaces:**
- Produces:
  - `_build_report_markdown(diagnosis_id: int) -> str | None`
  - `_markdown_to_html(markdown: str, title: str) -> str`
  - `export_diagnosis_report_html(diagnosis_id: int) -> str | None`
- `export_diagnosis_report(diagnosis_id: int) -> str | None` 签名不变。

- [ ] **Step 1: 写失败测试**

```python
from webapp import report_export


def test_html_report_escapes_and_contains_content(tmp_path, monkeypatch):
    saved = _seed_diagnosis(tmp_path, monkeypatch)
    monkeypatch.setattr(report_export, "REPORT_DIR", str(tmp_path / "reports"))
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE diagnosis_records SET question=? WHERE id=?",
            ("<script>alert(1)</script> 链路不通", saved["diagnosis_id"]),
        )

    path = report_export.export_diagnosis_report_html(saved["diagnosis_id"])

    assert path.endswith(".html")
    text = Path(path).read_text(encoding="utf-8")
    assert "<html" in text
    assert "故障诊断报告" in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert(1)</script>" not in text


def test_html_report_missing_diagnosis_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(report_export, "REPORT_DIR", str(tmp_path / "reports"))
    assert report_export.export_diagnosis_report_html(999999) is None
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_report_export_api.py -q
```

Expected: `AttributeError` / 函数不存在。

- [ ] **Step 3: 重构并实现**

把现有 `export_diagnosis_report()` 的内容构造移到 `_build_report_markdown()`，原函数只负责写 `.md`。
新增 `_markdown_to_html()`：转义 HTML，识别 `#/##/###` 标题、`-` 列表、数字列表和普通段落，
输出带内联打印样式的完整 HTML 文档。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_report_export_api.py -q
```

Expected: 旧 Markdown 测试 + 新 HTML 测试全部 PASS。

---

### Task 2: API 格式参数与前端入口

**Files:**
- Modify: `webapp/api/v1/router.py`
- Modify: `frontend/src/shared/api/client.ts`
- Modify: `frontend/src/shared/api/client.test.ts`
- Modify: `frontend/src/features/diagnosis/DiagnosisPage.tsx`
- Modify: `frontend/src/features/diagnosis/AiWorkspace.tsx`
- Modify: `tests/test_report_export_api.py`
- Modify: `docs/API.md`

**Interfaces:**
- `export_session_report(session_id: str, format: str = "md") -> FileResponse`
- `api.sessionReportUrl(sessionId: string, format: "md" | "html" = "md"): string`

- [ ] **Step 1: 写失败测试**

```python
def test_export_session_report_html_format(tmp_path, monkeypatch):
    saved = _seed_diagnosis(tmp_path, monkeypatch)
    response = export_session_report(saved["session_id"], format="html")
    assert Path(response.path).suffix == ".html"
    assert response.media_type == "text/html"


def test_export_session_report_rejects_unknown_format(tmp_path, monkeypatch):
    saved = _seed_diagnosis(tmp_path, monkeypatch)
    with pytest.raises(ApiProblem) as exc:
        export_session_report(saved["session_id"], format="pdf")
    assert exc.value.status == 400
```

```ts
// client.test.ts
it("adds the html format for the report url", () => {
  expect(api.sessionReportUrl("session/id", "html")).toBe(
    "/api/v1/sessions/session%2Fid/report?format=html"
  );
});
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_report_export_api.py -q
cd frontend && npm test -- --run src/shared/api/client.test.ts
```

Expected: API 不接受 `format`；前端函数只接受一个参数。

- [ ] **Step 3: 实现 API 与前端入口**

API 根据 `format` 调用 Markdown/HTML 导出，非法值返回
`ApiProblem(400, "INVALID_REPORT_FORMAT", ...)`。前端默认链接保持 Markdown，
新增“导出 HTML”链接；`sessionReportUrl` 增加可选 `format` 参数。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_report_export_api.py -q
cd frontend && npm test && npm run build
```

Expected: PASS。

---

### Task 3: 全量回归与提交

**Files:**
- Modify: `docs/API.md`

- [ ] **Step 1: 全量回归**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm test && npm run build
```

- [ ] **Step 2: 文档更新**

在 `docs/API.md` 的报告导出行补充：

```text
GET /api/v1/sessions/{session_id}/report?format=html
```

- [ ] **Step 3: Commit**

```bash
git add webapp/report_export.py webapp/api/v1/router.py tests/test_report_export_api.py \
  frontend/src/shared/api/client.ts frontend/src/shared/api/client.test.ts \
  frontend/src/features/diagnosis/DiagnosisPage.tsx frontend/src/features/diagnosis/AiWorkspace.tsx \
  docs/API.md docs/superpowers/specs/2026-10-26-html-report-export-design.md \
  docs/superpowers/plans/2026-10-26-html-report-export.md
git commit -m "feat(report): add printable html export"
```
