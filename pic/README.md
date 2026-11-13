# 配图目录（pic/）

本目录由 `scripts/generate_pic_figures.py` 根据 `/home/wyj/下载/配图清单.xlsx` 生成。

- 清单总数：**32**
- 本次生成：**14**（9 张示意图 + 3 张真实数据图 + 2 张实机截图组图）
- 清单标记已绘制：**18**（未在仓库中找到同名文件，本次未重新生成）

## 本次生成的图片

| 图号 | 图名 | 文件 |
|---|---|---|
| 图3-2 | VLAN 与 IP 地址规划图 | `fig3-2_vlan_ip_plan.png` |
| 图3-3 | 路由与 ACL 规划示意图 | `fig3-3_routing_acl_plan.png` |
| 图3-4 | 实验网接入链路图 | `fig3-4_access_link.png` |
| 图4-3 | 原始输出到状态事实的解析映射图 | `fig4-3_parse_mapping.png` |
| 图5-3 | 知识覆盖与检索命中图 | `fig5-3_kb_coverage_retrieval.png` |
| 图6-3 | 证据装配与配额示意图 | `fig6-3_evidence_quota.png` |
| 图7-3 | 诊断输出字段结构图 | `fig7-3_diagnosis_fields.png` |
| 图8-3 | 人工变更状态机图 | `fig8-3_change_state_machine.png` |
| 图9-2 | 有/无知识库准确率对比柱状图 | `fig9-2_kb_accuracy.png` |
| 图9-3 | 响应时间对比图 | `fig9-3_response_time.png` |
| 图9-4 | 证据一致性校验示意图 | `fig9-4_evidence_consistency.png` |
| 图10-1 | 启动流程与依赖检查图 | `fig10-1_startup_flow.png` |
| 图10-2 | Web 控制台界面组图 | `fig10-2_web_console.png` |
| 图10-3 | 报告导出样例图 | `fig10-3_report_sample.png` |

## 数据来源

- 图5-3、图9-2、图9-3：`data/ops.db` 中 2026-11-09 的真实 campaign 数据；
  知识库文章数来自 `knowledge_base/articles.json`。
- 图10-2、图10-3：实机运行 `python -m uvicorn webapp.main:app` 后由 Chrome/Playwright 截图；
  报告样例来自 `reports/diagnosis_17.html`。
- 图3-2、图3-3、图3-4、图4-3、图6-3、图7-3、图8-3、图9-4、图10-1：
  按清单“绘制内容要点”和项目代码/实测状态绘制。

## 原始截图

`screenshots/` 保留实机截图原件，便于复核或替换组图：

- `ui_overview.png`、`ui_assets.png`、`ui_diagnosis.png`、`ui_knowledge.png`、`ui_terminal.png`、`ui_mobile_overview.png`
- `report_export_first.png`、`report_export_manual.png`、`report_export_kb.png`

## 重新生成

```bash
python3 scripts/generate_pic_figures.py
```

> 注意：实机截图需要先启动 Web 服务，或复用 `screenshots/` 中的现有截图。

## 完整清单

见 `manifest.csv`（包含全部 32 条及状态）。