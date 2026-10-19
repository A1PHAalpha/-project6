"""Startup preparation and knowledge-base degradation tests."""
import json

import chromadb
import pytest

import app_bootstrap
import knowledge_base.retriever as retriever
from collector import db
from knowledge_base.build_index import COLLECTION_NAME
from knowledge_base.embedding import FastEmbedEmbeddingFunction
from knowledge_base.retriever import KnowledgeBaseUnavailable
from rag_engine import diagnosis_engine


def test_bootstrap_seeds_database_and_records_missing_kb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bootstrap.db"))
    monkeypatch.setattr(app_bootstrap, "CHROMA_PATH", str(tmp_path / "empty_chroma"))

    runtime = app_bootstrap.bootstrap_runtime()

    assert runtime["status"] == "degraded"
    assert runtime["database"]["status"] == "ready"
    assert runtime["database"]["device_count"] > 0
    assert runtime["knowledge_base"]["status"] == "missing"
    assert runtime["startup_collection"]["status"] == "disabled"
    assert db.get_system_status("knowledge_base")["status"] == "missing"
    assert db.get_system_status("runtime")["status"] == "degraded"


def test_inspect_knowledge_base_reports_empty_collection(tmp_path):
    chroma_path = tmp_path / "chroma"
    client = chromadb.PersistentClient(path=str(chroma_path))
    client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=FastEmbedEmbeddingFunction(),
    )

    status = app_bootstrap.inspect_knowledge_base(chroma_path=str(chroma_path))

    assert status["status"] == "empty"
    assert status["count"] == 0


def test_retrieve_rejects_missing_collection(tmp_path, monkeypatch):
    client = chromadb.PersistentClient(path=str(tmp_path / "missing_chroma"))
    monkeypatch.setattr(retriever, "get_chroma_client", lambda: client)
    retriever._collection = None

    with pytest.raises(KnowledgeBaseUnavailable):
        retriever.retrieve("OSPF 邻居不通")
    assert retriever._collection is None


def test_diagnose_degrades_when_knowledge_base_is_unavailable(monkeypatch):
    db.init_db()
    monkeypatch.setattr(
        diagnosis_engine,
        "retrieve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KnowledgeBaseUnavailable("索引缺失")
        ),
    )
    monkeypatch.setattr(
        diagnosis_engine,
        "chat_completion",
        lambda _messages: (
            json.dumps({"fault_phenomenon": "链路异常"}),
            12,
            {"model": "test-model"},
        ),
    )

    result = diagnosis_engine.diagnose("分支A路由器不通", use_kb=True)

    assert result["retrieved_docs"] == []
    assert result["knowledge_base"]["status"] == "error"
    assert "索引缺失" in result["knowledge_base"]["error"]
    assert result["error"] is None


def test_startup_collection_records_success(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "collection.db"))
    monkeypatch.setattr(
        app_bootstrap,
        "run_collection",
        lambda _devices=None, **_kwargs: {
            "mode": "full",
            "device_count": 9,
            "requested_devices": None,
            "missing_devices": [],
            "ssh_commands_success": 20,
            "ssh_commands_failed": 0,
            "snmp_devices_success": 6,
            "snmp_devices_failed": 0,
            "connectivity_tests": 2,
        },
    )

    status = app_bootstrap.run_startup_collection_once()

    assert status["status"] == "success"
    assert status["ssh_commands_success"] == 20
    assert db.get_system_status("startup_collection")["status"] == "success"


def test_startup_collection_records_missing_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "collection.db"))
    monkeypatch.setattr(
        app_bootstrap,
        "run_collection",
        lambda _devices=None, **_kwargs: (_ for _ in ()).throw(
            app_bootstrap.CollectionConfigurationError("缺少设备凭据")
        ),
    )

    status = app_bootstrap.run_startup_collection_once()

    assert status["status"] == "skipped"
    assert "缺少设备凭据" in status["message"]
