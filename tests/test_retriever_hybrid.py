import pytest

import knowledge_base.retriever as retriever
from knowledge_base.retriever import (
    _detect_category,
    _expand_query,
    _load_articles,
    _rank_articles,
    _tokenize,
)


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


def test_normal_query_detected_as_general_troubleshooting():
    assert (
        _detect_category("帮忙检查一下总部和分支A之间的网络现在是否正常。")
        == "通用排查方法"
    )


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


def test_link_down_query_ranks_interface_article_in_top_three():
    articles = _load_articles()
    distances = {article["id"]: 0.9 for article in articles}
    distances.update(
        {
            "KB-VLAN-003": 0.606,
            "KB-VLAN-001": 0.608,
            "KB-VLAN-002": 0.638,
            "KB-IF-002": 0.827,
            "KB-IF-001": 1.095,
        }
    )
    hits = _rank_articles("分支A路由器和交换机之间不通", articles, distances, top_k=3)
    assert {"KB-IF-001", "KB-IF-002"} & {hit["id"] for hit in hits}


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


class FakeCollection:
    def count(self):
        return 3

    def query(self, **_kwargs):
        return {
            "ids": [["KB-IF-002", "KB-VLAN-001", "KB-ROUTE-003"]],
            "documents": [["接口 line protocol down", "VLAN 端口错误", "OSPF 邻居异常"]],
            "metadatas": [
                [
                    {"title": "接口line protocol down", "category": "接口故障"},
                    {"title": "VLAN 端口错误", "category": "VLAN"},
                    {"title": "OSPF 邻居异常", "category": "路由"},
                ]
            ],
            "distances": [[0.40, 0.55, 0.70]],
        }


def _fake_articles():
    return [
        {
            "id": "KB-IF-002",
            "title": "接口line protocol down",
            "category": "接口故障",
            "content": "接口物理层状态为up但line protocol down，检查封装和时钟。",
            "applicable_device": "Cisco IOS",
            "version": "IOS 15.x",
            "source": "Cisco",
        },
        {
            "id": "KB-VLAN-001",
            "title": "VLAN 端口错误",
            "category": "VLAN",
            "content": "交换机端口未加入正确VLAN导致终端不通。",
            "applicable_device": "Cisco IOS",
            "version": "IOS 15.x",
            "source": "Cisco",
        },
        {
            "id": "KB-ROUTE-003",
            "title": "OSPF 邻居异常",
            "category": "路由",
            "content": "OSPF 邻居状态异常导致路由学习不完整。",
            "applicable_device": "Cisco IOS",
            "version": "IOS 15.x",
            "source": "Cisco",
        },
    ]


def test_retrieve_uses_hybrid_ranking(monkeypatch):
    monkeypatch.setattr(retriever, "_get_collection", lambda: FakeCollection())
    monkeypatch.setattr(retriever, "_load_articles", _fake_articles)

    hits = retriever.retrieve("接口 line protocol down", top_k=2)

    assert len(hits) == 2
    assert hits[0]["id"] == "KB-IF-002"
    assert "score" in hits[0]
    assert hits[0]["retrieval_method"] == "hybrid"


def test_retrieve_raises_when_index_missing(monkeypatch):
    def missing():
        raise retriever.KnowledgeBaseUnavailable("missing")

    monkeypatch.setattr(retriever, "_get_collection", missing)
    with pytest.raises(retriever.KnowledgeBaseUnavailable):
        retriever.retrieve("任意问题")
