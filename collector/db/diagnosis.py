"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ._core import _json_text, _json_value, _new_id, _sha256, _stable_id, get_conn, init_db, now_iso
from .assets import _store_blob_conn


def insert_diagnosis(
    question,
    model_id,
    prompt_template_id,
    llm_params,
    retrieved_doc_ids,
    evidence,
    answer,
    use_kb=1,
    response_time_ms=None,
):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO diagnosis_records
               (created_at, question, model_id, prompt_template_id, llm_params_json,
                retrieved_doc_ids_json, evidence_json, answer_json, use_kb, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now_iso(),
                question,
                model_id,
                prompt_template_id,
                json.dumps(llm_params, ensure_ascii=False),
                json.dumps(retrieved_doc_ids, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(answer, ensure_ascii=False),
                use_kb,
                response_time_ms,
            ),
        )
        return cur.lastrowid


def list_diagnoses(limit=50):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM diagnosis_records ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_diagnosis(diagnosis_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM diagnosis_records WHERE id=?", (diagnosis_id,)).fetchone()
    return dict(row) if row else None


def _next_message_seq_conn(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq_no), 0) FROM agent_messages WHERE session_id=?",
        (session_id,),
    ).fetchone()
    return int(row[0] or 0) + 1


def _kb_chunk_for_hit_conn(conn: sqlite3.Connection, hit: dict) -> sqlite3.Row | None:
    doc_id = str(hit.get("id") or hit.get("doc_id") or "")
    if not doc_id:
        return None
    if hit.get("version"):
        row = conn.execute(
            """SELECT * FROM kb_chunks
               WHERE index_name='ops_knowledge_base' AND doc_id=? AND version=?
               LIMIT 1""",
            (doc_id, hit.get("version")),
        ).fetchone()
        if row:
            return row
    return conn.execute(
        """SELECT * FROM kb_chunks
           WHERE index_name='ops_knowledge_base' AND doc_id=?
           ORDER BY created_at_utc DESC LIMIT 1""",
        (doc_id,),
    ).fetchone()


def _row_value(row: sqlite3.Row | None, key: str, default=None):
    if row is None or key not in row.keys():
        return default
    return row[key]


def _existing_id_conn(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    value: str | None,
) -> str | None:
    if not value:
        return None
    row = conn.execute(
        f'SELECT 1 FROM "{table}" WHERE "{column}"=? LIMIT 1',
        (value,),
    ).fetchone()
    return value if row else None


def save_diagnosis_run(
    *,
    question: str,
    model_id: str,
    prompt_template_id: str,
    prompt_version: dict | None,
    llm_params: dict | None,
    answer: dict,
    evidence: list,
    retrieved_docs: list,
    use_kb: bool,
    kb_status: str,
    kb_error: str | None,
    response_time_ms: int | None,
    total_response_time_ms: int | None,
    context_messages: list,
    requested_by: str = "web",
    session_id: str | None = None,
    model_status: str = "success",
    model_error_code: str | None = None,
    model_error_message: str | None = None,
    retrieval_filters: dict | None = None,
    top_k: int = 4,
) -> dict:
    """原子写入一次诊断的会话、上下文、检索、模型调用和结论引用。"""
    init_db()
    timestamp = now_iso()
    prompt_version_id = None
    params = dict(llm_params or {})
    answer_text = json.dumps(answer, ensure_ascii=False, sort_keys=True)

    with get_conn() as conn:
        if prompt_version:
            system_prompt = str(prompt_version.get("system_prompt") or "")
            user_template = str(prompt_version.get("user_template") or "")
            prompt_hash = _sha256(system_prompt + "\n" + user_template)
            prompt_version_id = _stable_id("promptver", f"{prompt_template_id}:{prompt_hash}")
            conn.execute(
                """INSERT OR IGNORE INTO prompt_versions
                   (prompt_version_id, template_id, system_prompt, user_template,
                    content_hash, is_active, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (
                    prompt_version_id,
                    prompt_template_id,
                    system_prompt,
                    user_template,
                    prompt_hash,
                    timestamp,
                ),
            )

        if session_id:
            session = conn.execute(
                "SELECT * FROM agent_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if not session:
                raise ValueError(f"诊断会话不存在: {session_id}")
        else:
            session_id = _new_id("session")
            session = None

        session_status = "completed" if model_status == "success" else "failed"
        if session:
            conn.execute(
                """UPDATE agent_sessions
                   SET model_id=?, prompt_version_id=COALESCE(?, prompt_version_id),
                       status=?, updated_at_utc=?, ended_at_utc=?
                   WHERE session_id=?""",
                (
                    model_id,
                    prompt_version_id,
                    session_status,
                    timestamp,
                    timestamp,
                    session_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO agent_sessions
                   (session_id, title, created_by, model_id, prompt_version_id,
                    status, started_at_utc, updated_at_utc, ended_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    (question[:100] or "未命名诊断会话"),
                    requested_by,
                    model_id,
                    prompt_version_id,
                    session_status,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

        seq = _next_message_seq_conn(conn, session_id)
        user_message_id = _new_id("msg")
        conn.execute(
            """INSERT INTO agent_messages
               (message_id, session_id, seq_no, role, content_text, content_json, created_at_utc)
               VALUES (?, ?, ?, 'user', ?, ?, ?)""",
            (
                user_message_id,
                session_id,
                seq,
                question,
                json.dumps({"question": question}, ensure_ascii=False),
                timestamp,
            ),
        )

        rendered_messages_json = json.dumps(context_messages, ensure_ascii=False)
        rendered_prompt_redacted = "\n\n".join(
            str(message.get("content") or "") for message in context_messages if isinstance(message, dict)
        )
        snapshot_id = _new_id("ctx")
        conn.execute(
            """INSERT INTO context_snapshots
               (snapshot_id, session_id, message_id, prompt_version_id,
                rendered_messages_json, rendered_prompt_redacted, prompt_hash,
                token_count, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                session_id,
                user_message_id,
                prompt_version_id,
                rendered_messages_json,
                rendered_prompt_redacted,
                _sha256(rendered_messages_json),
                max(1, len(rendered_prompt_redacted) // 4),
                timestamp,
            ),
        )

        normalized_kb_status = kb_status if kb_status in ("success", "empty", "disabled", "error") else "error"
        kb_index_version_id = next(
            (hit.get("index_version_id") for hit in (retrieved_docs or []) if hit.get("index_version_id")),
            None,
        )
        kb_index_version_id = _existing_id_conn(
            conn,
            "knowledge_index_versions",
            "index_version_id",
            kb_index_version_id,
        )
        query_id = _new_id("retrieval")
        conn.execute(
            """INSERT INTO retrieval_queries
               (query_id, session_id, message_id, query_text, filters_json,
                top_k, index_version_id, status, error_message, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                query_id,
                session_id,
                user_message_id,
                question,
                _json_text(retrieval_filters),
                top_k,
                kb_index_version_id,
                normalized_kb_status,
                kb_error,
                timestamp,
            ),
        )
        hits_by_doc_id: dict[str, list[dict]] = {}
        for rank, hit in enumerate(retrieved_docs or [], 1):
            chunk = _kb_chunk_for_hit_conn(conn, hit)
            doc_id = str(hit.get("id") or hit.get("doc_id") or "")
            if not doc_id:
                continue
            hit_id = _new_id("hit")
            content = hit.get("content") or (chunk["content"] if chunk else None)
            content_hash = chunk["content_hash"] if chunk else _sha256(str(content or ""))
            article_version_id = hit.get("article_version_id") or _row_value(chunk, "article_version_id")
            article_version_id = _existing_id_conn(
                conn,
                "knowledge_article_versions",
                "article_version_id",
                article_version_id,
            )
            index_version_id = (
                hit.get("index_version_id") or _row_value(chunk, "index_version_id") or kb_index_version_id
            )
            index_version_id = _existing_id_conn(
                conn,
                "knowledge_index_versions",
                "index_version_id",
                index_version_id,
            )
            source_url = hit.get("source_url") or _row_value(chunk, "source_url")
            conn.execute(
                """INSERT INTO retrieval_hits
                   (hit_id, query_id, chunk_id, rank_no, distance, doc_id, title,
                    category, applicable_device, version, source, content,
                    content_hash, article_version_id, index_version_id,
                    source_url, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    hit_id,
                    query_id,
                    chunk["chunk_id"] if chunk else None,
                    rank,
                    hit.get("distance"),
                    doc_id,
                    hit.get("title") or (chunk["title"] if chunk else None),
                    hit.get("category") or (chunk["category"] if chunk else None),
                    hit.get("applicable_device") or (chunk["applicable_device"] if chunk else None),
                    hit.get("version") or (chunk["version"] if chunk else None),
                    hit.get("source") or (chunk["source"] if chunk else None),
                    content,
                    content_hash,
                    article_version_id,
                    index_version_id,
                    source_url,
                    timestamp,
                ),
            )
            hit_record = {
                "hit_id": hit_id,
                "doc_id": doc_id,
                "rank_no": rank,
                "chunk": dict(chunk) if chunk else None,
            }
            hits_by_doc_id.setdefault(doc_id, []).append(hit_record)

        seq = _next_message_seq_conn(conn, session_id)
        assistant_message_id = _new_id("msg")
        conn.execute(
            """INSERT INTO agent_messages
               (message_id, session_id, seq_no, role, content_text, content_json, created_at_utc)
               VALUES (?, ?, ?, 'assistant', ?, ?, ?)""",
            (
                assistant_message_id,
                session_id,
                seq,
                answer_text,
                answer_text,
                timestamp,
            ),
        )

        provider = str(params.get("_provider") or "openai_compatible")
        usage = params.get("_usage") or {}
        public_params = {key: value for key, value in params.items() if not key.startswith("_")}
        model_call_id = _new_id("modelcall")
        response_blob_id = _store_blob_conn(conn, answer_text, "application/json")
        conn.execute(
            """INSERT INTO model_calls
               (call_id, session_id, message_id, context_snapshot_id, provider,
                model, request_id, params_json, input_tokens, output_tokens,
                latency_ms, status, error_code, error_message, response_blob_id,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model_call_id,
                session_id,
                assistant_message_id,
                snapshot_id,
                provider,
                model_id,
                params.get("_request_id"),
                json.dumps(public_params, ensure_ascii=False),
                usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                usage.get("completion_tokens") if isinstance(usage, dict) else None,
                response_time_ms,
                model_status if model_status in ("success", "error") else "error",
                model_error_code,
                model_error_message,
                response_blob_id,
                timestamp,
            ),
        )

        retrieved_doc_ids = [
            str(hit.get("id") or hit.get("doc_id"))
            for hit in (retrieved_docs or [])
            if hit.get("id") or hit.get("doc_id")
        ]
        cur = conn.execute(
            """INSERT INTO diagnosis_records
               (created_at, question, model_id, prompt_template_id, llm_params_json,
                retrieved_doc_ids_json, evidence_json, answer_json, use_kb,
                response_time_ms, session_id, retrieval_query_id,
                context_snapshot_id, model_call_id, kb_status,
                total_response_time_ms, index_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                question,
                model_id,
                prompt_template_id,
                json.dumps(public_params, ensure_ascii=False),
                json.dumps(retrieved_doc_ids, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(answer, ensure_ascii=False),
                1 if use_kb else 0,
                response_time_ms,
                session_id,
                query_id,
                snapshot_id,
                model_call_id,
                normalized_kb_status,
                total_response_time_ms,
                kb_index_version_id,
            ),
        )
        diagnosis_id = int(cur.lastrowid)

        causes = answer.get("possible_causes") or []
        root_cause = ""
        if causes and isinstance(causes[0], dict):
            root_cause = str(causes[0].get("cause") or "")
        confidence = (
            "high"
            if model_status == "success" and evidence and retrieved_docs
            else "medium"
            if model_status == "success" and evidence
            else "low"
        )
        conclusion_id = _new_id("conclusion")
        conn.execute(
            """INSERT INTO diagnosis_conclusions
               (conclusion_id, diagnosis_id, session_id, final_message_id,
                fault_phenomenon, root_cause, missing_information, confidence,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conclusion_id,
                diagnosis_id,
                session_id,
                assistant_message_id,
                answer.get("fault_phenomenon"),
                root_cause or None,
                answer.get("missing_information"),
                confidence,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE diagnosis_records SET conclusion_id=? WHERE id=?",
            (conclusion_id, diagnosis_id),
        )

        referenced_observation_ids = set()
        referenced_evidence_pairs = set()
        for item in answer.get("evidence_used") or []:
            if not isinstance(item, dict):
                continue
            if item.get("observation_id"):
                referenced_observation_ids.add(item["observation_id"])
            if item.get("device_name") and item.get("collected_at"):
                referenced_evidence_pairs.add((item["device_name"], item["collected_at"]))

        for evidence_item in evidence:
            if not isinstance(evidence_item, dict):
                continue
            observation_id = evidence_item.get("observation_id")
            item_id = evidence_item.get("item_id")
            if not observation_id and not item_id:
                continue
            if (
                observation_id
                and not conn.execute(
                    "SELECT 1 FROM state_observations WHERE observation_id=?",
                    (observation_id,),
                ).fetchone()
            ):
                observation_id = None
            if (
                item_id
                and not conn.execute(
                    "SELECT 1 FROM collection_items WHERE item_id=?",
                    (item_id,),
                ).fetchone()
            ):
                item_id = None
            if not observation_id and not item_id:
                continue
            referenced = bool(
                observation_id in referenced_observation_ids
                or (
                    evidence_item.get("device_name"),
                    evidence_item.get("collected_at"),
                )
                in referenced_evidence_pairs
            )
            conn.execute(
                """INSERT INTO evidence_links
                   (link_id, conclusion_id, link_type, observation_id, item_id,
                    retrieval_hit_id, tool_call_result_id, claim, role, created_at_utc)
                   VALUES (?, ?, 'device_evidence', ?, ?, NULL, NULL, ?, ?, ?)""",
                (
                    _new_id("evlink"),
                    conclusion_id,
                    observation_id,
                    item_id,
                    evidence_item.get("summary"),
                    "supporting" if referenced else "context",
                    timestamp,
                ),
            )

        referenced_kb_ids = set()
        for key in ("possible_causes", "fix_suggestions"):
            for item in answer.get(key) or []:
                if isinstance(item, dict):
                    referenced_kb_ids.update(str(ref) for ref in (item.get("kb_refs") or []))
        for doc_id, hit_records in hits_by_doc_id.items():
            for hit_record in hit_records:
                conn.execute(
                    """INSERT INTO evidence_links
                       (link_id, conclusion_id, link_type, observation_id, item_id,
                        retrieval_hit_id, tool_call_result_id, claim, role, created_at_utc)
                       VALUES (?, ?, 'knowledge', NULL, NULL, ?, NULL, ?, ?, ?)""",
                    (
                        _new_id("evlink"),
                        conclusion_id,
                        hit_record["hit_id"],
                        f"检索知识片段 {doc_id}",
                        "supporting" if doc_id in referenced_kb_ids else "context",
                        timestamp,
                    ),
                )

    return {
        "diagnosis_id": diagnosis_id,
        "session_id": session_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "context_snapshot_id": snapshot_id,
        "retrieval_query_id": query_id,
        "model_call_id": model_call_id,
        "conclusion_id": conclusion_id,
    }


def list_agent_sessions(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM agent_messages m
                       WHERE m.session_id=s.session_id) AS message_count,
                      (SELECT COUNT(*) FROM diagnosis_conclusions c
                       WHERE c.session_id=s.session_id) AS conclusion_count
               FROM agent_sessions s
               ORDER BY s.updated_at_utc DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_agent_session(session_id: str) -> dict | None:
    with get_conn() as conn:
        session = conn.execute(
            "SELECT * FROM agent_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not session:
            return None
        messages = conn.execute(
            """SELECT * FROM agent_messages
               WHERE session_id=? ORDER BY seq_no""",
            (session_id,),
        ).fetchall()
        contexts = conn.execute(
            """SELECT * FROM context_snapshots
               WHERE session_id=? ORDER BY created_at_utc""",
            (session_id,),
        ).fetchall()
        tool_calls = conn.execute(
            """SELECT tc.*, tr.tool_name, tr.transport
               FROM tool_calls tc
               JOIN tool_registry tr ON tr.tool_id=tc.tool_id
               WHERE tc.session_id=? ORDER BY tc.started_at_utc""",
            (session_id,),
        ).fetchall()
        tool_results = conn.execute(
            """SELECT tcr.*, tc.session_id, tr.tool_name
               FROM tool_call_results tcr
               JOIN tool_calls tc ON tc.tool_call_id=tcr.tool_call_id
               JOIN tool_registry tr ON tr.tool_id=tc.tool_id
               WHERE tc.session_id=? ORDER BY tcr.created_at_utc""",
            (session_id,),
        ).fetchall()
        model_calls = conn.execute(
            """SELECT * FROM model_calls
               WHERE session_id=? ORDER BY created_at_utc""",
            (session_id,),
        ).fetchall()
        conclusions = conn.execute(
            """SELECT * FROM diagnosis_conclusions
               WHERE session_id=? ORDER BY created_at_utc""",
            (session_id,),
        ).fetchall()
    return {
        "session": dict(session),
        "messages": [dict(row) for row in messages],
        "context_snapshots": [dict(row) for row in contexts],
        "tool_calls": [dict(row) for row in tool_calls],
        "tool_results": [dict(row) for row in tool_results],
        "model_calls": [dict(row) for row in model_calls],
        "conclusions": [dict(row) for row in conclusions],
    }


def get_diagnosis_detail(diagnosis_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM diagnosis_records WHERE id=?",
            (diagnosis_id,),
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        retrieved_docs = []
        if record.get("retrieval_query_id"):
            hits = conn.execute(
                """SELECT * FROM retrieval_hits
                   WHERE query_id=? ORDER BY rank_no""",
                (record["retrieval_query_id"],),
            ).fetchall()
            retrieved_docs = [
                {
                    "id": hit["doc_id"],
                    "hit_id": hit["hit_id"],
                    "chunk_id": hit["chunk_id"],
                    "rank": hit["rank_no"],
                    "distance": hit["distance"],
                    "title": hit["title"],
                    "category": hit["category"],
                    "applicable_device": hit["applicable_device"],
                    "version": hit["version"],
                    "source": hit["source"],
                    "content": hit["content"],
                    "content_hash": hit["content_hash"],
                }
                for hit in hits
            ]
        conclusion = None
        evidence_links = []
        changes = []
        if record.get("conclusion_id"):
            conclusion_row = conn.execute(
                """SELECT * FROM diagnosis_conclusions
                   WHERE conclusion_id=?""",
                (record["conclusion_id"],),
            ).fetchone()
            conclusion = dict(conclusion_row) if conclusion_row else None
            link_rows = conn.execute(
                """SELECT * FROM evidence_links
                   WHERE conclusion_id=? ORDER BY created_at_utc, link_id""",
                (record["conclusion_id"],),
            ).fetchall()
            evidence_links = [dict(link) for link in link_rows]
        if record.get("id"):
            change_rows = conn.execute(
                """SELECT * FROM change_requests
                   WHERE diagnosis_id=? OR conclusion_id=?
                   ORDER BY created_at_utc DESC""",
                (record["id"], record.get("conclusion_id")),
            ).fetchall()
            changes = [dict(change) for change in change_rows]

    session = get_agent_session(record["session_id"]) if record.get("session_id") else None
    return {
        "diagnosis": record,
        "retrieved_docs": retrieved_docs,
        "conclusion": conclusion,
        "evidence_links": evidence_links,
        "session": session,
        "changes": changes,
    }


def list_tools(enabled_only: bool = True) -> list[dict]:
    with get_conn() as conn:
        sql = "SELECT * FROM tool_registry"
        if enabled_only:
            sql += " WHERE enabled=1"
        rows = conn.execute(sql + " ORDER BY tool_name").fetchall()
    tools = []
    for row in rows:
        item = dict(row)
        item["input_schema"] = _json_value(item.pop("input_schema_json")) or {}
        tools.append(item)
    return tools


def get_tool(tool_name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tool_registry WHERE tool_name=?",
            (tool_name,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["input_schema"] = _json_value(item.pop("input_schema_json")) or {}
    return item


def start_tool_call(
    tool_name: str,
    arguments: dict | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
) -> dict:
    init_db()
    tool = get_tool(tool_name)
    if not tool:
        raise ValueError(f"工具未注册: {tool_name}")
    if not tool["enabled"]:
        raise ValueError(f"工具已禁用: {tool_name}")
    if not tool["read_only"]:
        raise ValueError(f"第二期只允许调用只读工具: {tool_name}")
    timestamp = now_iso()
    tool_call_id = _new_id("toolcall")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tool_calls
               (tool_call_id, session_id, message_id, tool_id, arguments_json,
                status, error_code, error_message, started_at_utc,
                finished_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, 'running', NULL, NULL, ?, NULL, ?)""",
            (
                tool_call_id,
                session_id,
                message_id,
                tool["tool_id"],
                json.dumps(arguments or {}, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
    return {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "tool": tool,
        "arguments": arguments or {},
        "started_at_utc": timestamp,
    }


def finish_tool_call(
    tool_call_id: str,
    *,
    status: str,
    result: Any = None,
    result_kind: str = "json",
    collection_run_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    timestamp = now_iso()
    result_text = json.dumps(result, ensure_ascii=False) if result is not None else "{}"
    with get_conn() as conn:
        call = conn.execute(
            "SELECT * FROM tool_calls WHERE tool_call_id=?",
            (tool_call_id,),
        ).fetchone()
        if not call:
            raise ValueError(f"工具调用不存在: {tool_call_id}")
        conn.execute(
            """UPDATE tool_calls
               SET status=?, error_code=?, error_message=?, finished_at_utc=?
               WHERE tool_call_id=?""",
            (status, error_code, error_message, timestamp, tool_call_id),
        )
        result_id = _new_id("toolresult")
        blob_id = _store_blob_conn(conn, result_text, "application/json")
        conn.execute(
            """INSERT INTO tool_call_results
               (result_id, tool_call_id, result_kind, collection_run_id,
                result_json, blob_id, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                result_id,
                tool_call_id,
                result_kind,
                collection_run_id,
                result_text,
                blob_id,
                timestamp,
            ),
        )
    return {
        "tool_call_id": tool_call_id,
        "result_id": result_id,
        "collection_run_id": collection_run_id,
        "status": status,
        "result": result,
        "finished_at_utc": timestamp,
    }


def get_tool_call(tool_call_id: str) -> dict | None:
    with get_conn() as conn:
        call = conn.execute(
            """SELECT tc.*, tr.tool_name, tr.transport, tr.read_only
               FROM tool_calls tc
               JOIN tool_registry tr ON tr.tool_id=tc.tool_id
               WHERE tc.tool_call_id=?""",
            (tool_call_id,),
        ).fetchone()
        if not call:
            return None
        result = conn.execute(
            """SELECT * FROM tool_call_results
               WHERE tool_call_id=?""",
            (tool_call_id,),
        ).fetchone()
    payload = dict(call)
    payload["arguments"] = _json_value(payload.pop("arguments_json")) or {}
    payload["result"] = dict(result) if result else None
    if payload["result"]:
        payload["result"]["result"] = _json_value(payload["result"].pop("result_json")) or {}
    return payload


__all__ = (
    "_existing_id_conn",
    "_kb_chunk_for_hit_conn",
    "_next_message_seq_conn",
    "_row_value",
    "finish_tool_call",
    "get_agent_session",
    "get_diagnosis",
    "get_diagnosis_detail",
    "get_tool",
    "get_tool_call",
    "insert_diagnosis",
    "list_agent_sessions",
    "list_diagnoses",
    "list_tools",
    "save_diagnosis_run",
    "start_tool_call",
)
