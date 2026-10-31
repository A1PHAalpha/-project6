# 需求核对整改实施计划（第一阶段）

**Goal:** 修复核对报告确认的高风险缺陷，使“正常网络误报默认路由缺失”“P5 复测目标错误”“多段命令仍可执行”“新控制台缺人工记录”四类问题可验证地关闭。

**Spec/来源:** `REQUIREMENTS_VERIFICATION.md` 第 5、8 节；`docs/ACCEPTANCE_CRITERIA.md`。

## Task 1: 保留默认路由和关键路由

- Modify: `rag_engine/evidence_builder.py`
- Test: `tests/test_evidence_selection.py`

要求：
- `0.0.0.0/0` / `S*` 路由优先级高于普通路由；
- 直连/本地路由优先于普通静态/动态路由；
- 在 4 条路由配额下，默认路由必须被保留；
- 增加回归测试。

## Task 2: Prompt 禁止把“证据未包含”写成“配置缺失”

- Modify: `rag_engine/prompt_templates.py`
- Test: `tests/test_suggestion_dto.py`

要求加入明确规则：
> 证据中没有出现的配置，只能写“本次证据未包含/无法确认”，不得写成“设备未配置/配置缺失”。

## Task 3: 答案后校验

- Create: `rag_engine/answer_validation.py`
- Modify: `rag_engine/diagnosis_engine.py`
- Modify: `rag_engine/llm_client.py`
- Test: `tests/test_answer_validation.py`

要求：
1. `fix_suggestions.command` 含 `;`、换行或多条命令时，置为 `None`，并把“需人工拆分”写入 action；
2. `recheck_method` 中出现“总部服务器”但 IP 是设备自身管理 IP 时，替换为台账中 HQ-SRV 的地址；
3. 无台账/无法解析时只加缺失提示，不猜测；
4. 所有校验在落库前执行。

## Task 4: React 诊断页补齐复测与人工处理记录

- Modify: `webapp/api/v1/router.py`
- Modify: `frontend/src/shared/types.ts`
- Modify: `frontend/src/features/diagnosis/AiWorkspace.tsx`
- Test: `tests/test_session_tool_dto.py`

要求：
- 会话 DTO 增加 `recheckMethod`、`manualActions`；
- React 诊断页展示“复测方法”和“人工处理记录”；
- 旧版页面保持兼容。

## Task 5: 只读验收审计脚本

- Create: `scripts/verify_acceptance.py`
- Create: `tests/test_verify_acceptance.py`

检查项：
- 设备、采集批次、观测、时间、原始证据、发布状态；
- 诊断/模型/检索/上下文记录；
- 知识库数量、类别、元数据、`source_url` 覆盖；
- `experiment_runs`、`experiment_results`、`fault_injections`、`change_requests`；
- 报告与备份文件是否存在；
- 输出 PASS/WARN/FAIL 和 JSON/Markdown 报告，不修改数据库。

## Task 6: 回归

- `pytest -q`
- `ruff check .`
- `frontend npm test && npm run build`
- 用真实 `data/ops.db` 运行审计脚本，确认：
  - 默认路由证据进入正常诊断；
  - P5 复测目标不再是 `192.168.249.12`；
  - 多段命令不再被标记为可执行；
  - 新控制台能看到复测/人工记录。

## 不在本阶段

- 26 篇知识文章的 source_url 补全（需要逐条核对来源，避免编造）；
- 4 类 × 2 真实故障注入与 20 次真实评测（下一阶段单独维护窗口执行）。
