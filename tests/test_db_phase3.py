"""Phase 3 schema, catalog, evaluation and retention tests."""
import sqlite3
import uuid

from collector import db


def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ops.db"))
    db.init_db()


def test_phase3_schema_and_default_governance(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    with db.get_conn() as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert version == db.SCHEMA_VERSION
    assert {
        "knowledge_sources",
        "knowledge_articles",
        "knowledge_article_versions",
        "knowledge_index_versions",
        "test_suites",
        "test_cases",
        "fault_injections",
        "experiment_runs",
        "experiment_results",
        "retention_policies",
        "maintenance_runs",
    } <= tables
    assert db.user_has_permission("admin", "change.approve")
    assert not db.user_has_permission("operator", "change.approve")
    assert {item["username"] for item in db.list_users()} >= {"admin", "system"}
    assert len(db.list_retention_policies()) >= 3


def test_v4_database_upgrades_to_phase3(tmp_path, monkeypatch):
    db_path = tmp_path / "v4.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(db.BASE_SCHEMA)
    conn.executescript(db.PHASE2_SCHEMA)
    conn.executescript(db.PHASE2B_SCHEMA)
    for version, name in (
        (1, "phase1_asset_collection_state"),
        (2, "normalize_utc_timestamps"),
        (3, "phase2_agent_sessions_tools_and_citations"),
        (4, "phase2b_human_change_and_operations"),
    ):
        conn.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (version, name, db.now_iso()),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    with db.get_conn() as upgraded:
        assert upgraded.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == db.SCHEMA_VERSION
        assert upgraded.execute(
            "SELECT COUNT(*) FROM knowledge_articles"
        ).fetchone()[0] == 26
        assert upgraded.execute(
            "SELECT COUNT(*) FROM roles"
        ).fetchone()[0] == 4


def test_knowledge_catalog_and_index_version_are_linked(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    article = {
        "id": "KB-ROUTE-001",
        "title": "缺少默认路由",
        "category": "路由",
        "content": "检查 show ip route 和默认路由。",
        "applicable_device": "Cisco IOS 路由器",
        "version": "phase3-test",
        "source": "测试来源",
        "source_url": "https://example.invalid/kb",
    }
    catalog = db.sync_knowledge_catalog([article])
    assert catalog["KB-ROUTE-001"]["article_version_id"]
    index_id = db.begin_knowledge_index(
        index_name="ops_knowledge_base",
        collection_name="ops_knowledge_base",
        model_name="test-embedding",
        dimension=8,
    )
    db.finish_knowledge_index(
        index_id,
        status="ready",
        article_count=1,
        content_hash="catalog-hash",
    )
    active = db.get_active_knowledge_index("ops_knowledge_base")
    assert active["index_version_id"] == index_id
    summary = db.knowledge_catalog_summary()
    assert summary["sources"] >= 1
    assert summary["active_index"]["index_version_id"] == index_id


def test_diagnosis_tolerates_cross_database_index_ids(tmp_path, monkeypatch):
    """评测使用 scratch 库时，Chroma 的索引 ID 可能不属于当前库。"""
    _temp_db(tmp_path, monkeypatch)
    db.upsert_device(
        {
            "device_name": "HQ-R1",
            "site": "总部",
            "device_type": "cisco_ios",
            "mgmt_ip": "10.0.0.1",
        }
    )
    db.insert_collection_batch(
        [
            {
                "device_name": "HQ-R1",
                "status": "success",
                "command": "show ip route",
                "raw_output": "Gateway of last resort is not set",
                "collected_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        trigger_type="synthetic",
    )
    bundle = db.get_current_device_bundle("HQ-R1")
    observation = bundle["observations"][0]
    result = db.save_diagnosis_run(
        question="路由是否正常",
        model_id="test",
        prompt_template_id="diagnosis_v1",
        prompt_version={"system_prompt": "s", "user_template": "u"},
        llm_params={},
        answer={"fault_phenomenon": "默认路由缺失", "possible_causes": []},
        evidence=[
            {
                "device_name": "HQ-R1",
                "collected_at": observation["observed_at_utc"],
                "status": "success",
                "observation_id": observation["observation_id"],
            }
        ],
        retrieved_docs=[
            {
                "id": "KB-ROUTE-001",
                "title": "默认路由",
                "version": "unknown",
                "index_version_id": "missing-index",
                "article_version_id": "missing-article-version",
            }
        ],
        use_kb=True,
        kb_status="success",
        kb_error=None,
        response_time_ms=1,
        total_response_time_ms=2,
        context_messages=[{"role": "user", "content": "路由是否正常"}],
    )
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT index_version_id, article_version_id FROM retrieval_hits WHERE query_id=?",
            (result["retrieval_query_id"],),
        ).fetchone()
    assert row["index_version_id"] is None
    assert row["article_version_id"] is None


def test_build_index_persists_catalog_and_active_version(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    import knowledge_base.build_index as build_index

    class FakeCollection:
        def __init__(self):
            self.rows = []

        def upsert(self, ids, documents, metadatas):
            self.rows.append((ids, documents, metadatas))

    class FakeClient:
        def __init__(self):
            self.collection = FakeCollection()

        def get_or_create_collection(self, *, name, embedding_function):
            return self.collection

    client = FakeClient()
    monkeypatch.setattr(build_index, "get_chroma_client", lambda: client)
    monkeypatch.setattr(build_index, "get_embedding_function", lambda: object())
    count = build_index.build_index()

    assert count == 26
    active = db.get_active_knowledge_index("ops_knowledge_base")
    assert active
    assert active["article_count"] == 26
    assert client.collection.rows[0][2][0]["index_version_id"] == active["index_version_id"]


def test_experiment_results_and_summary_are_persisted(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    suite_id = db.upsert_test_suite("测试套件", version="test")
    case_id = db.upsert_test_case(
        suite_id,
        external_id="CASE-1",
        category="route",
        question="路由是否正常？",
        expected={"must_mention": ["默认路由"]},
    )
    db.register_fault_injection(
        case_id,
        fault_type="default_route_missing",
        description="合成证据",
    )
    experiment_id = db.start_experiment_run(
        suite_id,
        model_id="test-model",
        provider="test-provider",
    )
    for enabled in (True, False):
        db.record_experiment_result(
            experiment_id,
            case_id,
            kb_enabled=enabled,
            correctness="correct" if enabled else "wrong",
            correctness_score=1.0 if enabled else 0.0,
            executability_score=1.0 if enabled else 0.5,
            answer={"fault_phenomenon": "默认路由缺失"},
            evaluation={"source": "pytest"},
        )
    db.finish_experiment_run(
        experiment_id,
        summary={"accuracy": 0.5},
    )

    detail = db.get_experiment(experiment_id)
    assert detail["status"] == "completed"
    assert len(detail["results"]) == 2
    assert detail["results"][0]["evaluation"]["source"] == "pytest"
    assert db.list_experiments()[0]["experiment_id"] == experiment_id


def test_retention_is_dry_run_first_and_deletes_only_orphans(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    old = "2000-01-01T00:00:00+00:00"
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO health_checks
               (check_id, component, status, details_json, checked_at_utc)
               VALUES (?, 'test', 'ready', '{}', ?)""",
            (db._new_id("health"), old),
        )
        conn.execute(
            """INSERT INTO raw_blobs
               (blob_id, sha256, media_type, size_bytes, content_text,
                storage_ref, created_at_utc)
               VALUES (?, ?, 'text/plain', 1, 'x', NULL, ?)""",
            (db._new_id("blob"), uuid.uuid4().hex, old),
        )

    preview = db_retention_dry_run()
    assert preview["dry_run"] is True
    assert any(item["candidates"] >= 1 for item in preview["policies"])

    from collector.retention import run_retention

    applied = run_retention(dry_run=False, created_by="pytest")
    assert applied["dry_run"] is False
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM health_checks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_blobs").fetchone()[0] == 0
    assert db.list_audit_log(target_type="maintenance_run")


def db_retention_dry_run():
    from collector.retention import run_retention

    return run_retention(dry_run=True, created_by="pytest")
