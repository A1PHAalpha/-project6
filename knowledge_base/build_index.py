"""
将 articles.json 中的运维资料向量化并写入 Chroma 持久化向量库。
使用本地 fastembed(ONNX) embedding 模型，避免资料内容外传给第三方 API。
"""
import json
import logging
import os
from pathlib import Path

import chromadb

from project_config import load_project_env

load_project_env()

from .embedding import MODEL_NAME, FastEmbedEmbeddingFunction

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

    client = get_chroma_client()
    ef = get_embedding_function()

    if rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
            logger.info("已删除旧集合，准备重建")
        except Exception:
            pass

    collection = client.get_or_create_collection(name=COLLECTION_NAME, embedding_function=ef)

    ids = [a["id"] for a in articles]
    documents = [f"{a['title']}\n{a['content']}" for a in articles]
    metadatas = [
        {
            "title": a["title"],
            "category": a["category"],
            "applicable_device": a["applicable_device"],
            "version": a["version"],
            "source": a["source"],
        }
        for a in articles
    ]

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    logger.info("已写入/更新 %d 条知识库资料到 Chroma 集合 '%s'", len(articles), COLLECTION_NAME)
    return len(articles)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="删除旧集合后重建索引")
    args = parser.parse_args()
    build_index(rebuild=args.rebuild)
