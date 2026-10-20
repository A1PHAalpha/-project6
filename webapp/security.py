"""
单口令 Token 鉴权（中间件 + 登录态）。

安全要点：
- 环境变量 APP_API_TOKEN 必须存在且长度 >= 16，否则应用启动即报错退出（避免"忘配 = 裸奔"）。
- 校验使用 secrets.compare_digest，防时序攻击。
- 三种携带方式：Authorization: Bearer <token>、X-API-Token: <token>、HttpOnly 登录 Cookie
  （Cookie 方式让页面内 fetch 自动携带，模板 JS 零改动）。
- 放行白名单：/login、/logout、/static/*。
- 未通过校验的请求返回 401，不泄露内部信息。
"""
import os
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE_NAME = "netops_token"
MIN_TOKEN_LEN = 16
PUBLIC_PATHS = ("/login", "/logout")
PUBLIC_PREFIXES = ("/static/",)

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def get_api_token() -> str:
    """读取并校验 APP_API_TOKEN；未配置或过短时抛错，使应用拒绝启动。"""
    token = os.getenv("APP_API_TOKEN")
    if not token or len(token) < MIN_TOKEN_LEN:
        raise RuntimeError(
            "必须配置 APP_API_TOKEN 且长度不小于 16。请在项目根从 .env.example "
            "创建 .env，并写入随机生成的令牌"
        )
    return token


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header_token = request.headers.get("X-API-Token")
    if header_token:
        return header_token.strip()
    return request.cookies.get(COOKIE_NAME)


def is_valid_token(provided: str | None) -> bool:
    return bool(provided) and secrets.compare_digest(provided, get_api_token())


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def login_redirect(request: Request) -> RedirectResponse:
    """Build the login redirect while preserving the requested in-app path."""
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    return RedirectResponse(
        url=f"/login?next={quote(next_path, safe='')}",
        status_code=302,
    )


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_public(request.url.path):
            return await call_next(request)
        if is_valid_token(_extract_token(request)):
            return await call_next(request)
        # 浏览器页面请求跳转登录页；API / 脚本请求返回 401
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return login_redirect(request)
        return JSONResponse({"error": "未授权"}, status_code=401)


router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    templates = Jinja2Templates(directory=_TEMPLATES_DIR)
    return templates.TemplateResponse(request, "login.html")


class LoginRequest(BaseModel):
    token: str


@router.post("/login")
def login(req: LoginRequest):
    if not is_valid_token(req.token):
        return JSONResponse({"error": "口令错误"}, status_code=401)
    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(COOKIE_NAME, req.token, httponly=True, samesite="strict", max_age=8 * 3600)
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp
