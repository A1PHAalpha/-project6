"""
知识库检索接口：供 RAG 诊断引擎调用。
返回结果包含相似度分数和完整来源信息，供 Web 界面展示"检索依据"。
"""
import hashlib
import json
import logging
import math
import re
from collections import Counter
from pathlib import Path

from .build_index import COLLECTION_NAME, get_chroma_client, get_embedding_function

logger = logging.getLogger("retriever")

_collection = None

_CATEGORY_RULES = (
    ("VLAN", ("vlan", "trunk", "native vlan", "svi", "子接口", "access 端口", "access口")),
    ("ACL", ("acl", "permit", "deny", "访问控制", "拦截")),
    ("DHCP", ("dhcp", "地址池", "租约", "中继", "获取地址")),
    (
        "路由",
        (
            "ospf",
            "bgp",
            "路由表",
            "默认路由",
            "静态路由",
            "下一跳",
            "路由协议",
            "路由环路",
        ),
    ),
    (
        "接口故障",
        (
            "接口",
            "链路",
            "down",
            "不通",
            "连不上",
            "断",
            "丢包",
            "crc",
            "双工",
            "速率",
            "mtu",
            "物理",
            "光模块",
            "网线",
            "line protocol",
        ),
    ),
    (
        "通用排查方法",
        ("是否正常", "正常吗", "无故障", "巡检", "检查一下"),
    ),
)

_QUERY_EXPANSIONS = {
    "不通": "接口 链路 down 中断 物理 连接",
    "连不上": "接口 链路 down 不可达 物理",
    "时通时不通": "接口 链路 双工 速率 crc 丢包",
    "断": "接口 链路 down 中断",
    "丢包": "接口 链路 crc 双工 速率 mtu",
    "慢": "拥塞 丢包 双工 速率 mtu",
    "down": "接口 链路 line protocol 物理",
    "line protocol": "接口 链路 封装 时钟 keepalive",
    "vlan": "vlan trunk access svi 子接口",
    "trunk": "vlan trunk allowed native",
    "acl": "acl permit deny 访问控制",
    "dhcp": "dhcp 地址池 租约 中继",
    "ospf": "ospf 邻居 路由",
    "bgp": "bgp 邻居 路由",
    "路由表": "路由表 默认路由 静态路由 下一跳",
    "默认路由": "路由表 默认路由 静态路由 下一跳",
}


class KnowledgeBaseUnavailable(RuntimeError):
    """Raised when the persisted knowledge base is missing or empty."""


def _detect_category(query: str) -> str | None:
    q = (query or "").lower()
    for category, terms in _CATEGORY_RULES:
        if any(term in q for term in terms):
            return category
    return None


def _expand_query(query: str) -> str:
    q = (query or "").lower()
    additions = [
        expansion
        for trigger, expansion in _QUERY_EXPANSIONS.items()
        if trigger in q
    ]
    if not additions:
        return query
    return f"{query} {' '.join(additions)}"


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9._/-]*", text)
    for sequence in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(
                sequence[index : index + 2]
                for index in range(len(sequence) - 1)
            )
    return tokens


def _load_articles() -> list[dict]:
    path = Path(__file__).with_name("articles.json")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)["articles"]


def _lexical_scores(query: str, articles: list[dict]) -> dict[str, float]:
    """BM25 over title + content using Chinese bigrams and ASCII tokens."""
    documents = [
        f"{article.get('title') or ''}\n{article.get('content') or ''}"
        for article in articles
    ]
    tokenized_documents = [_tokenize(document) for document in documents]
    query_tokens = set(_tokenize(_expand_query(query)))
    if not query_tokens or not documents:
        return {article["id"]: 0.0 for article in articles}

    document_count = len(documents)
    average_length = (
        sum(len(tokens) for tokens in tokenized_documents) / document_count
    ) or 1.0
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized_documents:
        document_frequency.update(set(tokens))

    k1, b = 1.5, 0.75
    scores: dict[str, float] = {}
    for article, tokens in zip(articles, tokenized_documents):
        term_frequency = Counter(tokens)
        length = len(tokens)
        score = 0.0
        for token in query_tokens:
            tf = term_frequency.get(token, 0)
            if not tf:
                continue
            df = document_frequency.get(token, 0)
            idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
            denominator = tf + k1 * (1 - b + b * length / average_length)
            score += idf * tf * (k1 + 1) / denominator
        scores[article["id"]] = score
    return scores


def _rank_articles(
    query: str,
    articles: list[dict],
    vector_distances: dict[str, float],
    top_k: int,
    category_filter: str | None = None,
) -> list[dict]:
    if top_k <= 0:
        return []

    detected_category = _detect_category(query)
    lexical_scores = _lexical_scores(query, articles)
    max_lexical = max(lexical_scores.values(), default=0.0)

    candidates = []
    for article in articles:
        if category_filter and article.get("category") != category_filter:
            continue
        article_id = article["id"]
        distance = vector_distances.get(article_id)
        vector_score = (
            max(0.0, 1.0 - float(distance)) if distance is not None else 0.0
        )
        lexical_score = lexical_scores.get(article_id, 0.0)
        lexical_norm = lexical_score / max_lexical if max_lexical > 0 else 0.0
        category_boost = (
            0.35
            if detected_category
            and article.get("category") == detected_category
            else 0.0
        )
        score = 0.45 * vector_score + 0.55 * lexical_norm + category_boost
        content = (
            f"{article.get('title') or ''}\n{article.get('content') or ''}"
        )
        candidates.append(
            (
                score,
                distance if distance is not None else 999.0,
                article,
                vector_score,
                lexical_score,
                content,
            )
        )

    candidates.sort(key=lambda item: (-item[0], item[1], item[2].get("id") or ""))

    hits = []
    for rank, (score, _, article, vector_score, lexical_score, content) in enumerate(
        candidates[:top_k], 1
    ):
        distance = vector_distances.get(article["id"])
        hits.append(
            {
                "id": article["id"],
                "title": article.get("title"),
                "category": article.get("category"),
                "applicable_device": article.get("applicable_device"),
                "version": article.get("version"),
                "source": article.get("source"),
                "source_url": article.get("source_url"),
                "applicable_platform": article.get("applicable_platform"),
                "article_version_id": article.get("article_version_id"),
                "index_version_id": article.get("index_version_id"),
                "content": content,
                "content_hash": article.get("content_hash")
                or hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "rank": rank,
                "distance": distance,
                "score": round(score, 6),
                "vector_score": round(vector_score, 6),
                "lexical_score": round(lexical_score, 6),
                "retrieval_method": "hybrid",
            }
        )
    return hits


def _get_collection():
    global _collection
    if _collection is None:
        client = get_chroma_client()
        ef = get_embedding_function()
        try:
            _collection = client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
        except Exception as exc:
            raise KnowledgeBaseUnavailable(
                f"知识库索引不存在或无法加载，请先执行索引初始化: {type(exc).__name__}: {exc}"
            ) from exc
        if _collection.count() == 0:
            _collection = None
            raise KnowledgeBaseUnavailable("知识库集合为空，请先执行索引初始化")
    return _collection


def retrieve(query: str, top_k: int = 4, category_filter: str = None):
    """
    检索与 query 最相关的 top_k 条知识库资料。

    先在全部知识文档上取向量距离，再与 BM25 词法分数、类别加成融合重排。
    category_filter: 可选，按类别过滤（VLAN/路由/DHCP/ACL/接口故障/通用排查方法）
    """
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        raise KnowledgeBaseUnavailable("知识库集合为空，请先执行索引初始化")
    top_k = min(top_k, count)
    if top_k <= 0:
        return []

    result = collection.query(query_texts=[query], n_results=count)
    ids = result.get("ids", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    if not ids:
        return []

    vector_distances = {
        article_id: float(distance)
        for article_id, distance in zip(ids, distances)
    }
    metadata_by_id = {
        article_id: metadata or {}
        for article_id, metadata in zip(ids, metadatas)
    }

    articles = []
    for article in _load_articles():
        if article["id"] not in vector_distances:
            continue
        enriched = dict(article)
        for key, value in metadata_by_id.get(article["id"], {}).items():
            if value is not None and key not in enriched:
                enriched[key] = value
        articles.append(enriched)

    hits = _rank_articles(
        query,
        articles,
        vector_distances,
        top_k=top_k,
        category_filter=category_filter,
    )
    logger.info("检索完成: query='%s' 返回 %d 条", query, len(hits))
    return hits
