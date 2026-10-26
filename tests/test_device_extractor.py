"""站点关键词 -> 设备映射单测。"""
from pathlib import Path

import pytest

from collector import db
from rag_engine.device_extractor import extract_relevant_devices

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def seed_devices():
    db.init_db()
    db.load_inventory(str(PROJECT_ROOT / "network" / "device_inventory.json"))
    yield


def test_site_keyword_maps_to_site_devices():
    names = extract_relevant_devices("分支A路由器和交换机之间不通")
    assert names
    assert all(n.startswith("BR-A") for n in names)


def test_unknown_site_returns_all_devices():
    all_names = {d["device_name"] for d in db.list_devices()}
    assert set(extract_relevant_devices("网络好像有点慢")) == all_names


def test_exact_device_names_do_not_expand_to_all_devices():
    names = extract_relevant_devices("BR-A-R2 和 BR-A-SW2 之间不通")
    assert set(names) == {"BR-A-R2", "BR-A-SW2"}


def test_device_name_is_case_insensitive():
    assert extract_relevant_devices("hq-r1 的 Gi0/1 状态怎么样") == ["HQ-R1"]


def test_management_ip_matches_device():
    assert extract_relevant_devices("192.168.249.11 的接口状态") == ["HQ-R1"]
