"""
知识库检索接口：供 RAG 诊断引擎调用。
返回结果包含相似度分数和完整来源信息，供 Web 界面展示"检索依据"。
"""
import logging

from .build_index import COLLECTION_NAME, get_chroma_client, get_embedding_function

logger = logging.getLogger("retriever")

_collection = None


class KnowledgeBaseUnavailable(RuntimeError):
    """Raised when the persisted knowledge base is missing or empty."""


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
    category_filter: 可选，按类别过滤（VLAN/路由/DHCP/ACL/接口故障/通用排查方法）
    返回: [{id, title, category, applicable_device, version, source, content, distance}]
    """
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        raise KnowledgeBaseUnavailable("知识库集合为空，请先执行索引初始化")
    top_k = min(top_k, count)
    where = {"category": category_filter} if category_filter else None

    result = collection.query(
        query_texts=[query],
        n_results=top_k,
        where=where,
    )

    hits = []
    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    for i in range(len(ids)):
        meta = metadatas[i]
        hits.append({
            "id": ids[i],
            "title": meta.get("title"),
            "category": meta.get("category"),
            "applicable_device": meta.get("applicable_device"),
            "version": meta.get("version"),
            "source": meta.get("source"),
            "content": documents[i],
            "distance": distances[i],
        })

    logger.info("检索完成: query='%s' 返回 %d 条", query, len(hits))
    return hits
