"""慢测试：真实构建索引并检索（需下载 fastembed 模型）。

默认不跑（pyproject 中 `-m 'not slow'` 排除），显式 `pytest -m slow` 才执行。
"""
import importlib

import pytest

pytestmark = pytest.mark.slow


def test_build_index_and_retrieve(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    import knowledge_base.build_index as bi
    importlib.reload(bi)
    assert bi.build_index(rebuild=True) == 26

    import knowledge_base.retriever as rt
    importlib.reload(rt)
    hits = rt.retrieve("分支A路由器和交换机之间不通", top_k=4)
    assert len(hits) == 4
