"""鉴权边界的同步单测。

受限执行环境中的 asyncio 事件循环可能无法完成 TestClient 启动，因此这里直接
验证中间件辅助函数和登录路由；HTTP 集成仍由正常 CI 环境执行。
"""
import os

from starlette.requests import Request

from webapp.main import app
from webapp.security import (
    LoginRequest,
    _extract_token,
    _is_public,
    is_valid_token,
    login,
    login_page,
    login_redirect,
    logout,
)

TOKEN = os.environ["APP_API_TOKEN"]


def _request(path="/api/diagnoses", method="GET", headers=None, cookies=None):
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    cookie_header = "; ".join(f"{key}={value}" for key, value in (cookies or {}).items())
    if cookie_header:
        raw_headers.append((b"cookie", cookie_header.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": app,
    }
    return Request(scope)


def test_no_token_is_invalid():
    assert is_valid_token(None) is False


def test_wrong_token_is_invalid():
    assert is_valid_token("wrong-token") is False


def test_header_token_ok():
    request = _request(headers={"X-API-Token": TOKEN})
    assert is_valid_token(_extract_token(request)) is True


def test_bearer_token_ok():
    request = _request(headers={"Authorization": f"Bearer {TOKEN}"})
    assert is_valid_token(_extract_token(request)) is True


def test_cookie_token_ok():
    request = _request(cookies={"netops_token": TOKEN})
    assert is_valid_token(_extract_token(request)) is True


def test_unauthenticated_html_redirect_preserves_safe_next_path():
    request = _request(
        path="/console/overview",
        headers={"Accept": "text/html"},
    )
    response = login_redirect(request)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=%2Fconsole%2Foverview"


def test_login_sets_http_only_cookie():
    response = login(LoginRequest(token=TOKEN))
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()


def test_login_wrong_token_401():
    response = login(LoginRequest(token="nope"))
    assert response.status_code == 401


def test_login_page_and_logout_route():
    page = login_page(_request(path="/login"))
    assert page.status_code == 200
    assert logout().status_code == 302


def test_public_paths_and_docs_disabled():
    assert _is_public("/login")
    assert _is_public("/static/style.css")
    assert not _is_public("/api/diagnoses")
    assert app.openapi_url is None
    assert app.docs_url is None
