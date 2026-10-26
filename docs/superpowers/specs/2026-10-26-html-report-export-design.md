# HTML 诊断报告导出设计

## 背景

当前 `GET /api/v1/sessions/{id}/report` 只返回 Markdown 文件。Markdown 适合留档，
但浏览器直接打开是纯文本，不能作为可打印的报告交付；项目又没有 PDF/Word 依赖。

## 目标

1. 保留现有 Markdown 导出不变；
2. 新增 `?format=html` 的 HTML 报告；
3. HTML 报告包含标题、元数据、证据、知识依据、结论和建议；
4. HTML 自带打印样式，浏览器可直接“打印为 PDF”；
5. 不新增 Python/npm 依赖，不修改数据库。

## 非目标

- 不实现服务端 PDF/Word；
- 不引入模板引擎；
- 不改变报告内容的数据来源；
- 不改变现有审计事件名称。

## 验收

- 不传 `format` 时仍返回 `.md`，旧测试通过；
- `format=html` 返回 `text/html`，内容含 `<html>`、报告标题和问题；
- 非法 `format` 返回 400；
- 前端诊断页提供 HTML 导出入口；
- 全量测试、ruff、前端 build 通过。
