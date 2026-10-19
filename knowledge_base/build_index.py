"""
将 articles.json 中的运维资料向量化并写入 Chroma 持久化向量库。
使用本地 fastembed(ONNX) embedding 模型，避免资料内容外传给第三方 API。
"""
import hashlib
import json
import logging
import os
from pathlib import Path

import chromadb

from project_config import load_project_env

load_project_env()

from .embedding import CACHE_DIR, MODEL_NAME, FastEmbedEmbeddingFunction

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_index")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTICLES_PATH = str(Path(__file__).resolve().parent / "articles.json")
CHROMA_PATH = os.getenv("CHROMA_DB_PATH") or str(PROJECT_ROOT / "data" / "chroma")
COLLECTION_NAME = "ops_knowledge_base"


def get_chroma_client(path: str | None = None):
    target_path = path or CHROMA_PATH
    os.makedirs(target_path, exist_ok=True)
    return chromadb.PersistentClient(path=target_path)


def get_embedding_function():
    return FastEmbedEmbeddingFunction(model_name=MODEL_NAME)


def build_index(rebuild: bool = False):
    with open(ARTICLES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    articles = data["articles"]

    from collector import db

    db.init_db()
    catalog = db.sync_knowledge_catalog(articles)
    catalog_hash = hashlib.sha256(
        json.dumps(
            {
                doc_id: meta.get("content_hash")
                for doc_id, meta in sorted(catalog.items())
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    index_version_id = db.begin_knowledge_index(
        index_name=COLLECTION_NAME,
        collection_name=COLLECTION_NAME,
        model_name=MODEL_NAME,
        dimension=512,
        cache_ref=CACHE_DIR,
        chroma_path=CHROMA_PATH,
        content_hash=catalog_hash,
    )

    client = get_chroma_client()
    ef = get_embedding_function()

    try:
        if rebuild:
            try:
                client.delete_collection(COLLECTION_NAME)
                logger.info("已删除旧集合，准备重建")
            except Exception:
                pass

        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
        )

        ids = [a["id"] for a in articles]
        documents = [f"{a['title']}\n{a['content']}" for a in articles]
        metadatas = []
        for article in articles:
            meta = catalog.get(article["id"], {})
            metadatas.append(
                {
                    "title": article["title"],
                    "category": article["category"],
                    "applicable_device": article["applicable_device"],
                    "version": article["version"],
                    "source": article["source"],
                    "article_version_id": meta.get("article_version_id"),
                    "source_id": meta.get("source_id"),
                    "source_url": meta.get("source_url"),
                    "applicable_platform": meta.get("applicable_platform"),
                    "index_version_id": index_version_id,
                    "content_hash": meta.get("content_hash"),
                }
            )

        try:
            existing_ids = set(collection.get(include=[])["ids"])
            stale_ids = sorted(existing_ids - set(ids))
            if stale_ids:
                collection.delete(ids=stale_ids)
        except Exception:
            # 兼容只实现 upsert 的测试替身或旧 Chroma 客户端。
            pass

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        db.finish_knowledge_index(
            index_version_id,
            status="ready",
            article_count=len(articles),
            content_hash=catalog_hash,
        )
        db.record_audit_event(
            actor="knowledge_builder",
            action="knowledge.index.ready",
            target_type="knowledge_index",
            target_id=index_version_id,
            details={
                "collection": COLLECTION_NAME,
                "model": MODEL_NAME,
                "article_count": len(articles),
                "content_hash": catalog_hash,
            },
        )
        logger.info(
            "已写入/更新 %d 条知识库资料到 Chroma 集合 '%s'，索引版本 %s",
            len(articles),
            COLLECTION_NAME,
            index_version_id,
        )
        return len(articles)
    except Exception as exc:
        db.finish_knowledge_index(
            index_version_id,
            status="failed",
            article_count=0,
            content_hash=catalog_hash,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        db.record_audit_event(
            actor="knowledge_builder",
            action="knowledge.index.failed",
            target_type="knowledge_index",
            target_id=index_version_id,
            details={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="删除旧集合后重建索引")
    args = parser.parse_args()
    build_index(rebuild=args.rebuild)
