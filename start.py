#!/usr/bin/env python3
"""Single application entry point for NetOps Console.

The launcher checks the local runtime, installs missing dependencies, builds the
React console, and runs FastAPI in one process. The default is an isolated Mock
session; use ``--live`` for real APIs and device collection.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPT_PATH = Path(__file__).resolve()
VENV_DIR = PROJECT_ROOT / ".venv"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
REQUIRED_PYTHON = (3, 12)
MIN_NODE_MAJOR = 18
RUNTIME_MODULES = (
    "fastapi",
    "uvicorn",
    "jinja2",
    "pydantic",
    "openai",
    "dotenv",
    "netmiko",
    "pysnmp",
    "chromadb",
    "fastembed",
)
KNOWLEDGE_COLLECTION = "ops_knowledge_base"


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _is_project_interpreter() -> bool:
    """Return True only when the running interpreter's venv prefix is this project.

    ``.venv/bin/python`` is usually a symlink to the system interpreter, so
    comparing the executable files themselves would incorrectly report that
    the system interpreter is already inside the project venv.
    """
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


def _ensure_project_interpreter(create_missing: bool = True) -> None:
    if sys.version_info < REQUIRED_PYTHON:
        required = ".".join(map(str, REQUIRED_PYTHON))
        raise RuntimeError(f"需要 Python {required} 或更高版本，当前为 {sys.version.split()[0]}")

    target = _venv_python()
    if not target.exists():
        if not create_missing:
            raise RuntimeError("未找到 .venv；请先运行 python start.py 完成首次环境准备")
        print(f"[环境] 未找到 .venv，使用 {sys.executable} 创建...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], cwd=PROJECT_ROOT, check=True)
    if _is_project_interpreter():
        return

    print(f"[环境] 切换到项目虚拟环境：{target}", flush=True)
    os.execv(str(target), [str(target), str(SCRIPT_PATH), *sys.argv[1:]])


def _missing_modules() -> list[str]:
    missing = []
    for module in RUNTIME_MODULES:
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        if not available:
            missing.append(module)
    return missing


def _install_python_dependencies() -> None:
    if not _is_project_interpreter():
        raise RuntimeError(
            "依赖安装被拒绝：当前不是项目 .venv 解释器。请从项目根目录重新运行 "
            "python start.py，启动器会自动切换虚拟环境。"
        )
    requirements = (
        PROJECT_ROOT / "requirements" / "dev.txt",
        PROJECT_ROOT / "requirements" / "collect.txt",
    )
    command = [sys.executable, "-m", "pip", "install"]
    for path in requirements:
        command.extend(["-r", str(path)])
    print("[依赖] 安装 Python 依赖，首次执行可能需要数分钟...", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def _ensure_python_dependencies(skip_install: bool) -> None:
    missing = _missing_modules()
    if not missing:
        print("[依赖] Python 依赖完整", flush=True)
        return
    if skip_install:
        raise RuntimeError(f"缺少 Python 依赖: {', '.join(missing)}；请去掉 --skip-install 后重试")
    print(f"[依赖] 缺少模块: {', '.join(missing)}", flush=True)
    _install_python_dependencies()
    missing = _missing_modules()
    if missing:
        raise RuntimeError(f"Python 依赖安装后仍缺少: {', '.join(missing)}")


def _command_version(command: Path, *args: str) -> str:
    result = subprocess.run(
        [str(command), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _frontend_tools(require_node: bool) -> tuple[Path | None, Path | None]:
    node_text = shutil.which("node")
    npm_text = shutil.which("npm")
    if not require_node and (not node_text or not npm_text):
        return None, None
    if not node_text or not npm_text:
        raise RuntimeError("未找到 Node.js/npm；请安装 Node.js 18+ 后重试")

    node = Path(node_text)
    npm = Path(npm_text)
    node_version = _command_version(node, "--version").lstrip("v")
    try:
        major = int(node_version.split(".", 1)[0])
    except ValueError as exc:
        raise RuntimeError(f"无法识别 Node.js 版本: {node_version}") from exc
    if major < MIN_NODE_MAJOR:
        raise RuntimeError(f"需要 Node.js {MIN_NODE_MAJOR}+，当前为 {node_version}")
    print(f"[环境] Node.js {node_version}，npm {_command_version(npm, '--version')}", flush=True)
    return node, npm


def _read_env_value(name: str) -> str:
    from dotenv import dotenv_values

    return str(dotenv_values(ENV_PATH).get(name, ""))


def _ensure_env_file(create_if_missing: bool) -> bool:
    if ENV_PATH.exists():
        return False
    if not create_if_missing:
        raise RuntimeError("未找到项目根 .env；请先运行 python start.py 完成首次配置")
    if not ENV_EXAMPLE_PATH.exists():
        raise RuntimeError("未找到 .env.example，无法生成 .env")

    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    token = secrets.token_urlsafe(32)
    text = text.replace("APP_API_TOKEN=CHANGE_ME", f"APP_API_TOKEN={token}")
    ENV_PATH.write_text(text, encoding="utf-8")
    print("[配置] 已从 .env.example 创建 .env", flush=True)
    print(f"[配置] 首次登录令牌: {token}", flush=True)
    return True


def _validate_app_settings() -> tuple[str, int, str]:
    token = _read_env_value("APP_API_TOKEN")
    if len(token) < 16 or token == "CHANGE_ME":
        raise RuntimeError("APP_API_TOKEN 缺失、过短或仍为 CHANGE_ME；请在项目根 .env 中设置至少 16 位令牌")
    host = _read_env_value("APP_HOST") or "127.0.0.1"
    port_text = _read_env_value("APP_PORT") or "8000"
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError(f"APP_PORT 不是有效端口: {port_text}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"APP_PORT 超出范围: {port}")
    return host, port, token


def _port_is_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _ensure_frontend_dependencies(npm: Path, skip_install: bool) -> None:
    node_modules = FRONTEND_DIR / "node_modules"
    if node_modules.is_dir():
        print("[构建] 前端依赖已安装", flush=True)
        return
    if skip_install:
        raise RuntimeError("缺少 frontend/node_modules；请去掉 --skip-install 后重试")
    lock_file = FRONTEND_DIR / "package-lock.json"
    command = [str(npm), "ci"] if lock_file.exists() else [str(npm), "install"]
    print("[依赖] 安装前端依赖...", flush=True)
    subprocess.run(command, cwd=FRONTEND_DIR, check=True)


def _chroma_path() -> Path:
    raw = _read_env_value("CHROMA_DB_PATH") or "./data/chroma"
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _knowledge_index_count() -> int:
    path = _chroma_path()
    print(f"[内容] 检查知识库索引：{path}", flush=True)
    if not path.exists():
        return 0
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(path))
        collections = {item.name for item in client.list_collections()}
        if KNOWLEDGE_COLLECTION not in collections:
            return 0
        return client.get_collection(name=KNOWLEDGE_COLLECTION).count()
    except Exception as exc:
        print(f"[内容] 索引状态读取失败，将尝试重建：{type(exc).__name__}: {exc}", flush=True)
        return 0


def _ensure_knowledge_index(skip_rebuild: bool) -> None:
    count = _knowledge_index_count()
    if count > 0:
        print(f"[内容] 知识库索引可用，共 {count} 条资料", flush=True)
        return
    if skip_rebuild:
        raise RuntimeError("知识库索引缺失或为空，且指定了 --skip-index")
    print("[内容] 知识库索引缺失或为空，开始构建（首次可能需要下载模型）...", flush=True)
    subprocess.run(
        [sys.executable, "-m", "knowledge_base.build_index", "--rebuild"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    count = _knowledge_index_count()
    if count <= 0:
        raise RuntimeError("知识库构建完成，但索引中仍没有资料")
    print(f"[内容] 知识库索引构建完成，共 {count} 条资料", flush=True)


def _build_frontend(npm: Path, mock: bool) -> None:
    env = os.environ.copy()
    env["VITE_USE_MOCK_API"] = "true" if mock else "false"
    mode = "Mock" if mock else "Live"
    print(f"[构建] 构建 React 控制台（{mode} 数据模式）...", flush=True)
    subprocess.run([str(npm), "run", "build"], cwd=FRONTEND_DIR, env=env, check=True)
    if not (FRONTEND_DIR / "dist" / "index.html").is_file():
        raise RuntimeError("前端构建完成但未找到 frontend/dist/index.html")


def _wait_until_ready(process: subprocess.Popen, url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"FastAPI 提前退出，返回码 {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.4)
    raise RuntimeError(f"服务在 {timeout:.0f} 秒内未就绪: {url}")


def _stop_process(process: subprocess.Popen) -> None:
    print("\n[停止] 正在确认 Web 服务进程...", flush=True)
    if process.poll() is not None:
        print("[停止] Web 服务已退出", flush=True)
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    print("[停止] Web 服务已退出", flush=True)


def _run_server(host: str, port: int, mock: bool) -> int:
    if not _port_is_available(host, port):
        raise RuntimeError(f"端口已被占用：http://{host}:{port}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["VITE_USE_MOCK_API"] = "true" if mock else "false"
    if mock:
        env["APP_AUTO_COLLECT"] = "false"
        env["APP_RELOAD"] = "false"

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "webapp.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    process = subprocess.Popen(command, cwd=PROJECT_ROOT, env=env)
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    display_host = probe_host
    url = f"http://{display_host}:{port}"

    def interrupt_handler(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, interrupt_handler)

    try:
        _wait_until_ready(process, f"{url}/login")
        print("", flush=True)
        print("=" * 68, flush=True)
        print("NetOps Console 已启动", flush=True)
        print(f"运行模式: {'MOCK（示例数据，不连接真实设备）' if mock else 'LIVE（真实 API）'}", flush=True)
        print(f"访问地址: {url}", flush=True)
        print("按 Ctrl+C 停止服务并回收进程", flush=True)
        print("=" * 68, flush=True)
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        _stop_process(process)


def _check_only(mock: bool) -> None:
    _ensure_env_file(create_if_missing=False)
    host, port, _token = _validate_app_settings()
    _frontend_tools(require_node=True)
    if not (FRONTEND_DIR / "node_modules").is_dir():
        raise RuntimeError("缺少 frontend/node_modules")
    if not (FRONTEND_DIR / "dist" / "index.html").is_file():
        raise RuntimeError("缺少 frontend/dist/index.html")
    if not _port_is_available(host, port):
        raise RuntimeError(f"端口已被占用：{host}:{port}")
    mode = "Mock" if mock else "Live"
    index_count = _knowledge_index_count()
    if index_count <= 0:
        raise RuntimeError("知识库索引缺失或为空；首次启动会自动构建")
    print(f"[检查] 环境、配置、知识库、前端构建和端口均可用（当前会以 {mode} 模式启动）", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NetOps Console 单一启动入口")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", dest="mock", action="store_true", help="使用示例数据（默认）")
    mode.add_argument("--live", dest="mock", action="store_false", help="使用真实 API 和设备采集")
    parser.set_defaults(mock=True)
    parser.add_argument("--check", action="store_true", help="只检查环境，不安装、构建或启动")
    parser.add_argument("--skip-install", action="store_true", help="缺少依赖时不自动安装")
    parser.add_argument("--skip-build", action="store_true", help="复用现有 frontend/dist")
    parser.add_argument("--skip-index", action="store_true", help="知识库索引缺失时不自动构建")
    return parser.parse_args()


def main() -> int:
    _configure_console()
    args = _parse_args()
    try:
        if args.mock and args.skip_build:
            raise RuntimeError("Mock 模式必须重新构建前端，不能同时使用 --skip-build")
        mode = "MOCK（示例数据，不连接真实设备）" if args.mock else "LIVE（真实 API）"
        _ensure_project_interpreter(create_missing=not args.check)
        print("[1/6] 检查 Python 运行环境与依赖", flush=True)
        _ensure_python_dependencies(skip_install=args.skip_install or args.check)
        print("[2/6] 检查项目配置", flush=True)
        _ensure_env_file(create_if_missing=not args.check)

        if args.check:
            _check_only(args.mock)
            return 0

        host, port, _token = _validate_app_settings()
        print(f"启动模式：{mode}", flush=True)
        print("[3/6] 检查知识库内容", flush=True)
        _ensure_knowledge_index(args.skip_index)
        print("[4/6] 检查 Node.js 与前端依赖", flush=True)
        _node, npm = _frontend_tools(require_node=not args.skip_build)
        if npm is not None:
            _ensure_frontend_dependencies(npm, args.skip_install)
            if not args.skip_build:
                print("[5/6] 构建前端内容", flush=True)
                _build_frontend(npm, args.mock)
            elif not (FRONTEND_DIR / "dist" / "index.html").is_file():
                raise RuntimeError("已跳过构建，但 frontend/dist/index.html 不存在")
        elif not (FRONTEND_DIR / "dist" / "index.html").is_file():
            raise RuntimeError("未找到 Node.js，且现有前端构建产物不存在")
        else:
            print("[5/6] 未找到 Node.js，复用现有前端构建产物", flush=True)

        print("[6/6] 检查端口并启动服务", flush=True)
        return _run_server(host, port, args.mock)
    except KeyboardInterrupt:
        print("\n[停止] 启动已取消", flush=True)
        return 130
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"\n启动失败: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
