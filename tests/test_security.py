"""接口鉴权单测：无/错 token → 401，正确 token（Header/Cookie）→ 200，白名单放行。"""
import os

import pytest
from fastapi.testclient import TestClient

from webapp.main import app

TOKEN = os.environ["APP_API_TOKEN"]


@pytest.fixture
def client():
    # 每个用例独立 client，避免登录 Cookie 在用例间串扰
    with TestClient(app) as test_client:
        yield test_client


def test_no_token_returns_401(client):
    assert client.get("/api/diagnoses").status_code == 401


def test_wrong_token_returns_401(client):
    assert client.get("/api/diagnoses", headers={"X-API-Token": "wrong-token"}).status_code == 401


def test_header_token_ok(client):
    assert client.get("/api/diagnoses", headers={"X-API-Token": TOKEN}).status_code == 200


def test_bearer_token_ok(client):
    r = client.get("/api/diagnoses", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_login_then_cookie_ok(client):
    r = client.post("/login", json={"token": TOKEN})
    assert r.status_code == 200
    assert client.get("/api/diagnoses").status_code == 200


def test_login_wrong_token_401(client):
    assert client.post("/login", json={"token": "nope"}).status_code == 401


def test_login_page_public(client):
    r = client.get("/login", headers={"accept": "text/html"})
    assert r.status_code == 200


def test_static_files_public(client):
    assert client.get("/static/style.css").status_code != 401


def test_docs_disabled(client):
    assert client.get("/docs").status_code in (401, 404)
