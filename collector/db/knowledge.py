"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ._core import _new_id, _sha256, _stable_id, get_conn, init_db, now_iso


def sync_knowledge_catalog(
    articles: list[dict],
    *,
    retrieved_at_utc: str | None = None,
) -> dict[str, dict]:
    """把文章、来源和版本写入三期知识目录，返回 doc_id 元数据。"""
    init_db()
    with get_conn() as conn:
        return _sync_knowledge_catalog_conn(
            conn,
            articles,
            retrieved_at_utc=retrieved_at_utc,
        )


def begin_knowledge_index(
    *,
    index_name: str,
    collection_name: str,
    model_name: str,
    runtime: str = "fastembed-onnx",
    dimension: int | None = None,
    cache_ref: str | None = None,
    chroma_path: str | None = None,
    content_hash: str | None = None,
) -> str:
    """创建一次索引构建记录，成功后再由 finish_knowledge_index 发布。"""
    init_db()
    timestamp = now_iso()
    model_id = _stable_id("embmodel", f"{runtime}:{model_name}")
    index_version_id = _new_id("kbindex")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO embedding_models
               (model_id, model_name, dimension, runtime, cache_ref, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(model_name, runtime) DO UPDATE SET
                 dimension=COALESCE(excluded.dimension, embedding_models.dimension),
                 cache_ref=COALESCE(excluded.cache_ref, embedding_models.cache_ref)""",
            (model_id, model_name, dimension, runtime, cache_ref, timestamp),
        )
        conn.execute(
            """INSERT INTO knowledge_index_versions
               (index_version_id, index_name, collection_name, embedding_model_id,
                chroma_path, article_count, content_hash, status, is_active,
                error_message, built_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, 0, ?, 'building', 0, NULL, NULL, ?)""",
            (
                index_version_id,
                index_name,
                collection_name,
                model_id,
                chroma_path,
                content_hash,
                timestamp,
            ),
        )
    return index_version_id


def finish_knowledge_index(
    index_version_id: str,
    *,
    status: str,
    article_count: int = 0,
    content_hash: str | None = None,
    error_message: str | None = None,
) -> dict:
    """发布或标记一次知识库索引构建。"""
    if status not in ("ready", "failed", "retired"):
        raise ValueError("status 必须是 ready、failed 或 retired")
    init_db()
    timestamp = now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_index_versions WHERE index_version_id=?",
            (index_version_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"知识库索引不存在: {index_version_id}")
        index_name = row["index_name"]
        conn.execute(
            """UPDATE knowledge_index_versions
               SET status=?, article_count=?, content_hash=COALESCE(?, content_hash),
                   error_message=?, built_at_utc=?, is_active=?
               WHERE index_version_id=?""",
            (
                status,
                article_count,
                content_hash,
                error_message,
                timestamp if status in ("ready", "failed") else row["built_at_utc"],
                1 if status == "ready" else 0,
                index_version_id,
            ),
        )
        if status == "ready":
            conn.execute(
                """UPDATE knowledge_index_versions
                   SET is_active=0,
                       status=CASE WHEN status='ready' THEN 'retired' ELSE status END
                   WHERE index_name=? AND index_version_id<>?""",
                (index_name, index_version_id),
            )
            conn.execute(
                "UPDATE kb_chunks SET is_active=0 WHERE index_name=?",
                (index_name,),
            )
            conn.execute(
                """UPDATE kb_chunks
                   SET index_version_id=?, is_active=1
                   WHERE index_name=?
                     AND article_version_id IN (
                         SELECT article_version_id FROM knowledge_article_versions
                         WHERE status='active'
                     )""",
                (index_version_id, index_name),
            )
    return {
        "index_version_id": index_version_id,
        "index_name": index_name,
        "status": status,
        "article_count": article_count,
        "content_hash": content_hash,
        "error_message": error_message,
    }


def list_knowledge_indexes(
    index_name: str | None = None,
    limit: int = 20,
) -> list[dict]:
    with get_conn() as conn:
        sql = """SELECT kiv.*, em.model_name, em.dimension, em.runtime,
                        em.cache_ref
                 FROM knowledge_index_versions kiv
                 LEFT JOIN embedding_models em
                   ON em.model_id=kiv.embedding_model_id"""
        params: list[Any] = []
        if index_name:
            sql += " WHERE kiv.index_name=?"
            params.append(index_name)
        sql += " ORDER BY kiv.created_at_utc DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_active_knowledge_index(index_name: str = "ops_knowledge_base") -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT kiv.*, em.model_name, em.dimension, em.runtime
               FROM knowledge_index_versions kiv
               LEFT JOIN embedding_models em
                 ON em.model_id=kiv.embedding_model_id
               WHERE kiv.index_name=? AND kiv.is_active=1
               ORDER BY kiv.created_at_utc DESC LIMIT 1""",
            (index_name,),
        ).fetchone()
    return dict(row) if row else None


def knowledge_catalog_summary() -> dict:
    with get_conn() as conn:
        counts = {
            "sources": conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0],
            "articles": conn.execute("SELECT COUNT(*) FROM knowledge_articles").fetchone()[0],
            "active_versions": conn.execute(
                "SELECT COUNT(*) FROM knowledge_article_versions WHERE status='active'"
            ).fetchone()[0],
            "chunks": conn.execute("SELECT COUNT(*) FROM kb_chunks WHERE is_active=1").fetchone()[0],
        }
    return {
        **counts,
        "active_index": get_active_knowledge_index(),
        "indexes": list_knowledge_indexes(limit=10),
    }


def _knowledge_publisher(source: str) -> str:
    lowered = source.lower()
    if "cisco" in lowered and "社区" in source:
        return "Cisco Community"
    if "cisco" in lowered:
        return "Cisco"
    return "通用/社区"


def _knowledge_platform(applicable_device: str | None) -> str:
    value = str(applicable_device or "")
    if "Cisco" in value:
        return "Cisco IOS / IOSvL2"
    if "通用" in value:
        return "通用"
    return value or "未知"


def _sync_knowledge_catalog_conn(
    conn: sqlite3.Connection,
    articles: list[dict],
    *,
    retrieved_at_utc: str | None = None,
) -> dict[str, dict]:
    """同步知识文章、来源和版本，返回 doc_id -> 版本元数据。"""
    result: dict[str, dict] = {}
    timestamp = now_iso()
    retrieved = retrieved_at_utc or timestamp
    for article in articles:
        if not isinstance(article, dict):
            continue
        doc_id = str(article.get("id") or "").strip()
        if not doc_id:
            continue
        title = str(article.get("title") or doc_id)
        category = article.get("category")
        source_title = str(article.get("source") or "未记录来源")
        version_label = str(article.get("version") or "unknown")
        content = str(article.get("content") or "")
        applicable_device = article.get("applicable_device")
        applicable_platform = article.get("applicable_platform") or _knowledge_platform(applicable_device)
        source_url = article.get("source_url")
        edition = article.get("edition") or version_label
        license_name = article.get("license")
        source_key = _sha256(source_title)[:24]
        source_id = _stable_id("kbsrc", source_key)
        conn.execute(
            """INSERT INTO knowledge_sources
               (source_id, source_key, title, publisher, source_url, edition,
                license, retrieved_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_key) DO UPDATE SET
                 title=excluded.title,
                 publisher=excluded.publisher,
                 source_url=COALESCE(excluded.source_url, knowledge_sources.source_url),
                 edition=excluded.edition,
                 license=COALESCE(excluded.license, knowledge_sources.license),
                 retrieved_at_utc=excluded.retrieved_at_utc""",
            (
                source_id,
                source_key,
                source_title,
                _knowledge_publisher(source_title),
                source_url,
                edition,
                license_name,
                retrieved,
                timestamp,
            ),
        )

        article_id = _stable_id("kbart", doc_id)
        conn.execute(
            """INSERT INTO knowledge_articles
               (article_id, doc_id, title, category, created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(doc_id) DO UPDATE SET
                 title=excluded.title,
                 category=excluded.category,
                 updated_at_utc=excluded.updated_at_utc""",
            (article_id, doc_id, title, category, timestamp, timestamp),
        )

        content_hash = _sha256(
            json.dumps(
                {
                    "title": title,
                    "content": content,
                    "source": source_title,
                    "version": version_label,
                    "applicable_device": applicable_device,
                    "applicable_platform": applicable_platform,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        article_version_id = _stable_id("kbver", f"{doc_id}:{version_label}:{content_hash}")
        conn.execute(
            """INSERT INTO knowledge_article_versions
               (article_version_id, article_id, version_label, source_id,
                applicable_device, applicable_platform, content, content_hash,
                status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
               ON CONFLICT(article_id, version_label, content_hash) DO UPDATE SET
                 source_id=excluded.source_id,
                 applicable_device=excluded.applicable_device,
                 applicable_platform=excluded.applicable_platform,
                 status='active'""",
            (
                article_version_id,
                article_id,
                version_label,
                source_id,
                applicable_device,
                applicable_platform,
                content,
                content_hash,
                timestamp,
            ),
        )
        conn.execute(
            """UPDATE knowledge_article_versions
               SET status='retired'
               WHERE article_id=? AND article_version_id<>? AND status='active'""",
            (article_id, article_version_id),
        )
        result[doc_id] = {
            "doc_id": doc_id,
            "article_id": article_id,
            "article_version_id": article_version_id,
            "source_id": source_id,
            "source_url": source_url,
            "applicable_platform": applicable_platform,
            "content_hash": content_hash,
            "version_label": version_label,
        }
        conn.execute(
            """UPDATE kb_chunks
               SET article_version_id=?, source_id=?, source_url=?,
                   applicable_platform=?, chunk_no=COALESCE(chunk_no, 1),
                   content_hash=?, is_active=1
               WHERE index_name='ops_knowledge_base' AND doc_id=? AND version=?""",
            (
                article_version_id,
                source_id,
                source_url,
                applicable_platform,
                content_hash,
                doc_id,
                version_label,
            ),
        )
    return result


__all__ = (
    "_knowledge_platform",
    "_knowledge_publisher",
    "_sync_knowledge_catalog_conn",
    "begin_knowledge_index",
    "finish_knowledge_index",
    "get_active_knowledge_index",
    "knowledge_catalog_summary",
    "list_knowledge_indexes",
    "sync_knowledge_catalog",
)
