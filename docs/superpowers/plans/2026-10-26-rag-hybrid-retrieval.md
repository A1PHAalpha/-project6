# RAG 混合召回与重排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不引入新依赖的前提下，让链路/接口类问题稳定召回接口故障文章，同时保持 VLAN、路由、ACL、DHCP 的召回质量。

**Architecture:** 保留 `retrieve()` 公共接口和 Chroma 作为向量通道；新增纯函数负责查询扩展、类别检测、BM25 词法打分和融合重排。先对全部知识文档取向量距离，再与词法分数、类别加成合并排序。

**Tech Stack:** Python 3.12、Chroma、FastEmbed、pytest。

**Spec:** `docs/superpowers/specs/2026-10-26-rag-hybrid-retrieval-design.md`

## Global Constraints

- 不新增依赖。
- 保持 `retrieve(query, top_k=4, category_filter=None)` 签名兼容。
- 返回字段保留 `id/title/category/applicable_device/version/source/content/content_hash/rank/distance`。
- 新增字段只增不改：`score`、`vector_score`、`lexical_score`、`retrieval_method`。
- 无索引时继续抛 `KnowledgeBaseUnavailable`。
- 融合权重固定为 0.45 向量、0.55 词法、0.35 类别加成。

## Review Focus

- “路由器”不能被误判为路由类别；只有“路由表/默认路由/OSPF/BGP/下一跳”等才触发路由。测试在 Task 1。
- 链路不通问题必须让 `KB-IF-002` 或 `KB-IF-001` 进入 Top 3。测试在 Task 2。
- VLAN、OSPF、ACL、DHCP 的既有 Top 1 不能被混合排序破坏。测试在 Task 3。
- 空知识库/无索引仍必须抛 `KnowledgeBaseUnavailable`，不能返回空列表。测试在 Task 3。

---

### Task 1: 查询扩展与类别检测

**Files:**
- Modify: `knowledge_base/retriever.py`
- Create: `tests/test_retriever_hybrid.py`

**Interfaces:**
- Produces:
  - `_expand_query(query: str) -> str`
  - `_detect_category(query: str) -> str | None`
  - `_tokenize(text: str) -> list[str]`

- [ ] **Step 1: 写失败测试**

```python
from knowledge_base.retriever import _detect_category, _expand_query, _tokenize


def test_link_down_query_detected_as_interface_fault():
    assert _detect_category("分支A路由器和交换机之间不通") == "接口故障"


def test_router_word_alone_does_not_mean_route_category():
    assert _detect_category("分支A路由器和交换机之间不通") != "路由"
    assert _detect_category("路由器 CPU 有点高") is None


def test_vlan_and_route_and_acl_and_dhcp_categories():
    assert _detect_category("VLAN 10 放通了但 trunk 没放行") == "VLAN"
    assert _detect_category("OSPF 邻居起不来，路由表缺条目") == "路由"
    assert _detect_category("ACL 把业务流量 deny 了") == "ACL"
    assert _detect_category("DHCP 地址池耗尽") == "DHCP"


def test_link_down_query_expands_to_interface_terms():
    expanded = _expand_query("路由器与交换机之间不通")
    assert "接口" in expanded
    assert "链路" in expanded
    assert "down" in expanded


def test_tokenize_keeps_ascii_and_chinese_bigrams():
    tokens = _tokenize("OSPF 邻居 down")
    assert "ospf" in tokens
    assert "down" in tokens
    assert "邻居" in tokens
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_retriever_hybrid.py -q
```

Expected: `ImportError`，函数尚不存在。

- [ ] **Step 3: 实现纯函数**

实现查询扩展表、类别检测顺序（VLAN → ACL → DHCP → 路由 → 接口故障）、
中文 bigram + ASCII token 分词。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_retriever_hybrid.py -q
```

Expected: PASS。

---

### Task 2: BM25 词法打分与融合重排

**Files:**
- Modify: `knowledge_base/retriever.py`
- Modify: `tests/test_retriever_hybrid.py`

**Interfaces:**
- Produces:
  - `_load_articles() -> list[dict]`
  - `_lexical_scores(query: str, articles: list[dict]) -> dict[str, float]`
  - `_rank_articles(query: str, articles: list[dict], vector_distances: dict[str, float], top_k: int, category_filter: str | None = None) -> list[dict]`

- [ ] **Step 1: 写失败测试**

```python
from knowledge_base.retriever import _lexical_scores, _load_articles, _rank_articles


def test_link_down_query_ranks_interface_article_in_top_three():
    articles = _load_articles()
    scores = _lexical_scores("分支A路由器和交换机之间不通", articles)
    ranked = sorted(scores, key=scores.get, reverse=True)
    assert {"KB-IF-001", "KB-IF-002"} & set(ranked[:3])


def test_hybrid_rank_keeps_category_article_on_top():
    articles = _load_articles()
    distances = {article["id"]: 0.9 for article in articles}
    hits = _rank_articles("VLAN 10 不通", articles, distances, top_k=3)
    assert hits[0]["id"].startswith("KB-VLAN")


def test_hybrid_rank_returns_requested_fields():
    articles = _load_articles()
    distances = {article["id"]: 0.8 for article in articles}
    hits = _rank_articles("接口 line protocol down", articles, distances, top_k=2)
    assert len(hits) == 2
    assert {"id", "title", "content", "distance", "score", "rank"} <= set(hits[0])
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_retriever_hybrid.py -q
```

Expected: 函数不存在或排序不含接口文章。

- [ ] **Step 3: 实现 BM25 与融合排序**

BM25 使用 `k1=1.5`、`b=0.75`；词法分数按本次最大值归一化；
融合分数 `0.45 * (1 - distance) + 0.55 * lexical_norm + 0.35 * category_boost`。
空词法分数时只用向量分数。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_retriever_hybrid.py -q
```

Expected: PASS。

---

### Task 3: 接入 retrieve 并保持降级行为

**Files:**
- Modify: `knowledge_base/retriever.py`
- Modify: `tests/test_retriever_hybrid.py`

**Interfaces:**
- Consumes: 现有 `_get_collection()`。
- Produces: `retrieve()` 返回混合排序结果，保留原有字段并新增 `score` 等字段。

- [ ] **Step 1: 写失败测试**

```python
import pytest

import knowledge_base.retriever as retriever


class FakeCollection:
    def count(self):
        return 3

    def query(self, **_kwargs):
        return {
            "ids": [["KB-IF-002", "KB-VLAN-001", "KB-ROUTE-003"]],
            "documents": [["接口 line protocol down", "VLAN 端口错误", "OSPF 邻居异常"]],
            "metadatas": [[
                {"title": "接口line protocol down", "category": "接口故障"},
                {"title": "VLAN 端口错误", "category": "VLAN"},
                {"title": "OSPF 邻居异常", "category": "路由"},
            ]],
            "distances": [[0.40, 0.55, 0.70]],
        }


def test_retrieve_uses_hybrid_ranking(monkeypatch):
    monkeypatch.setattr(retriever, "_get_collection", lambda: FakeCollection())
    hits = retriever.retrieve("接口 line protocol down", top_k=2)
    assert len(hits) == 2
    assert hits[0]["id"] == "KB-IF-002"
    assert "score" in hits[0]


def test_retrieve_raises_when_index_missing(monkeypatch):
    def missing():
        raise retriever.KnowledgeBaseUnavailable("missing")

    monkeypatch.setattr(retriever, "_get_collection", missing)
    with pytest.raises(retriever.KnowledgeBaseUnavailable):
        retriever.retrieve("任意问题")
```

- [ ] **Step 2: 运行并确认失败**

```bash
.venv/bin/python -m pytest tests/test_retriever_hybrid.py -q
```

Expected: `retrieve` 尚未使用混合排序。

- [ ] **Step 3: 接入 retrieve**

1. 取 collection，`count = collection.count()`；
2. 查询全部文档：`n_results=count`；
3. 构造 `vector_distances`；
4. 调用 `_rank_articles`；
5. 按 `top_k` 和 `category_filter` 截断；
6. 保留原有元数据和 `content_hash` 逻辑；
7. 无索引/空集合继续抛 `KnowledgeBaseUnavailable`。

- [ ] **Step 4: 运行测试并确认通过**

```bash
.venv/bin/python -m pytest tests/test_retriever_hybrid.py -q
```

Expected: PASS。

---

### Task 4: 真实索引与诊断回归

**Files:**
- No production changes expected.

- [ ] **Step 1: 真实索引召回检查**

```bash
.venv/bin/python - <<'PY'
from knowledge_base.retriever import retrieve
for query in [
    "分支A路由器和交换机之间不通",
    "接口 line protocol down",
    "OSPF 邻居起不来",
    "VLAN 10 不通",
    "ACL 拦截业务流量",
    "DHCP 地址池耗尽",
]:
    print(query, [h["id"] for h in retrieve(query, top_k=4)])
PY
```

Expected: 链路不通 Top 3 含 `KB-IF-001/002`；其余问题命中对应类别。

- [ ] **Step 2: 诊断链路回归**

用合成 `LINK_DOWN_01` + 真实 LLM 诊断，确认：

- `model_status == "success"`；
- 引用知识条目包含 `KB-IF-001/002`，不再只有 `KB-GEN-001`。

- [ ] **Step 3: 全量回归**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm test && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add knowledge_base/retriever.py tests/test_retriever_hybrid.py docs/superpowers/specs/2026-10-26-rag-hybrid-retrieval-design.md docs/superpowers/plans/2026-10-26-rag-hybrid-retrieval.md
git commit -m "feat(rag): add hybrid lexical and category reranking"
```
