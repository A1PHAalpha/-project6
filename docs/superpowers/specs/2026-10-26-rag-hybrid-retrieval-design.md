# RAG 混合召回与重排设计

## 背景

当前 `knowledge_base/retriever.py` 只做向量检索。实测：

- `"分支A路由器和交换机之间不通"` 的 Top 4 是 VLAN/路由文章，
  Top 10 没有 `KB-IF-001/002`；
- `"接口 line protocol down"` 又能正确命中 `KB-IF-002`；
- 真实诊断因此引用了 `KB-GEN-001`，而不是接口故障文章。

知识库只有 26 篇短文章，适合在现有 Chroma 向量检索上叠加轻量词法检索和类别重排，
不需要引入新依赖或独立检索服务。

## 目标

1. 对“链路/接口不通”类问题，`KB-IF-001/002` 进入 Top 3；
2. 不破坏现有正确召回：VLAN、OSPF、ACL、DHCP 问题仍命中对应类别；
3. 返回结果保留原有字段（`id/title/content/distance/rank/...`），新增 `score` 等诊断字段；
4. 所有排序逻辑是可单测的纯函数；
5. 没有 Chroma 索引时仍抛 `KnowledgeBaseUnavailable`，诊断引擎继续降级。

## 方案

- 词法：中文 bigram + ASCII token 的 BM25；
- 查询扩展：把“不通/断/丢包/down”等症状扩展为接口、链路、物理层关键词；
- 类别检测：VLAN / 路由 / ACL / DHCP / 接口故障；
- 融合分数：`0.45 * vector + 0.55 * lexical_norm + 0.35 * category_boost`；
- 对全部 26 篇文档查询向量距离，再统一重排，避免只看向量 Top N。

## 非目标

- 不引入 jieba、rank_bm25、cross-encoder 等新依赖；
- 不修改知识文章内容；
- 不改变 `/api/v1` 契约和数据库结构；
- 不实现模型驱动的检索工具循环。
