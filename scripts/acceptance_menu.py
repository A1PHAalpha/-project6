#!/usr/bin/env python3
"""NetOps Console 一键验收菜单（默认只读）.

用法::

    .venv/bin/python scripts/acceptance_menu.py

输入菜单编号即可运行对应验收项；不会自动修改设备配置。
故障演练项只打印命令和检查已有记录，真正的写操作仍由人工在审计终端完成。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
    urlopen,
)

ROOT = Path(__file__).resolve().parents[1]
PY = Path(sys.executable)
FRONTEND = ROOT / "frontend"
BASE_URL = "http://127.0.0.1:8000"
AUDIT_JSON = Path("/tmp/netops_acceptance_menu.json")

# Load .env before resolving the database path, so SQLITE_DB_PATH is honoured.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass
DB = Path(os.getenv("SQLITE_DB_PATH", str(ROOT / "data" / "ops.db")))

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"
if not sys.stdout.isatty():
    GREEN = YELLOW = RED = CYAN = BOLD = RESET = ""


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def line(char: str = "=", n: int = 78) -> None:
    print(char * n)


def title(text: str) -> None:
    line()
    print(c(f" {text}", BOLD))
    line()


def add(results: list, status: str, label: str, detail: str = "") -> None:
    results.append((status, label, detail))


def show(results: list) -> tuple[int, int, int]:
    for status, label, detail in results:
        if status == "PASS":
            tag = c("[PASS]", GREEN)
        elif status == "WARN":
            tag = c("[WARN]", YELLOW)
        elif status == "INFO":
            tag = c("[INFO]", CYAN)
        else:
            tag = c("[FAIL]", RED)
        print(f"  {tag} {label}" + (f"  {detail}" if detail else ""))
    total = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for status, _, _ in results:
        total[status] = total.get(status, 0) + 1
    print()
    print(
        f"  小结：{c(str(total['PASS']) + ' PASS', GREEN)} / "
        f"{c(str(total['WARN']) + ' WARN', YELLOW)} / "
        f"{c(str(total['FAIL']) + ' FAIL', RED)} / "
        f"{c(str(total.get('INFO', 0)) + ' INFO', CYAN)}"
    )
    return total["PASS"], total["WARN"], total["FAIL"]


def run(cmd, *, timeout: int = 120, cwd: Path = ROOT):
    try:
        proc = subprocess.run(
            [str(x) for x in cmd],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError as exc:
        return 127, f"命令不存在: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（{timeout}s）"


def load_env() -> dict:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass
    return dict(os.environ)


def db_conn():
    if not DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def db_conn_rw():
    if not DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def scalar(conn, sql: str, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0


def http_open(path: str, *, opener=None, timeout: float = 4.0):
    url = BASE_URL + path
    try:
        if opener is None:
            with urlopen(url, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "ignore")
        with opener.open(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "ignore") if exc.fp else ""
    except (URLError, TimeoutError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def http_login():
    token = load_env().get("APP_API_TOKEN", "")
    if not token:
        return None, "APP_API_TOKEN 未配置"
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    try:
        req = Request(
            BASE_URL + "/login",
            data=json.dumps({"token": token}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=5) as resp:
            if resp.status != 200:
                return None, f"登录返回 {resp.status}"
    except Exception as exc:
        return None, f"登录失败: {type(exc).__name__}: {exc}"
    return opener, ""


def http_post_json(opener, path: str, payload: dict, timeout: float = 120.0):
    try:
        req = Request(
            BASE_URL + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore") if exc.fp else ""
        return exc.code, {"error": body}
    except Exception as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def service_running() -> bool:
    code, _ = http_open("/login")
    return code == 200


def parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------
def check_environment() -> list:
    results = []
    title("1. 环境与网络接入")

    py_ok = sys.version_info >= (3, 12)
    add(results, "PASS" if py_ok else "FAIL", "Python 版本",
        f"{sys.version_info.major}.{sys.version_info.minor}（要求 >=3.12）")

    venv_py = ROOT / ".venv" / "bin" / "python"
    add(results, "PASS" if venv_py.exists() else "WARN", "项目虚拟环境",
        str(venv_py) if venv_py.exists() else "未找到 .venv")

    for tool in ("node", "npm"):
        path = shutil.which(tool)
        add(results, "PASS" if path else "WARN", f"{tool}", path or "未安装/未在 PATH")

    env = load_env()
    required = [
        "APP_API_TOKEN", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "DEVICE_USERNAME", "DEVICE_PASSWORD", "APP_HOST", "APP_PORT",
        "SQLITE_DB_PATH", "CHROMA_DB_PATH", "EMBEDDING_MODEL",
    ]
    missing = [k for k in required if not (env.get(k) or "").strip()]
    add(results, "PASS" if not missing else "FAIL", ".env 必需配置",
        "齐全" if not missing else "缺少: " + ", ".join(missing))
    token = env.get("APP_API_TOKEN", "")
    add(results, "PASS" if len(token) >= 16 else "FAIL", "APP_API_TOKEN 长度",
        f"{len(token)}（要求 >=16）")

    add(results, "PASS" if DB.exists() else "FAIL", "SQLite 数据库",
        str(DB) if DB.exists() else "不存在")

    for host, port, label in (
        ("10.10.146.218", 80, "EVE HTTP"),
        ("10.10.146.218", 32769, "EVE 控制台"),
        ("192.168.249.11", 22, "设备 SSH HQ-R1"),
    ):
        try:
            socket.create_connection((host, port), timeout=2.0).close()
            add(results, "PASS", label, f"{host}:{port} 可达")
        except OSError as exc:
            add(results, "WARN", label, f"{host}:{port} 不可达（{exc}）")

    add(results, "INFO", "提示", "网络 WARN 只表示当前 Live 采集/演练不可用，不是代码失败。"
        if any(s == "WARN" for s, _, _ in results) else "网络检查完成。")
    return results


def check_startup() -> list:
    results = []
    title("2. 启动、登录与鉴权")

    code, output = run([PY, ROOT / "start.py", "--check"], timeout=120)
    tail = [line_text for line_text in output.splitlines() if line_text.strip()][-2:]
    add(results, "PASS" if code == 0 else "FAIL", "start.py --check",
        " | ".join(tail) if tail else f"exit={code}")

    if not service_running():
        add(results, "WARN", "Web 服务",
            "未启动；先运行 .venv/bin/python start.py --live")
        return results

    add(results, "PASS", "Web 服务", f"{BASE_URL}/login 返回 200")

    code, _ = http_open("/api/v1/overview")
    add(results, "PASS" if code == 401 else "WARN", "未认证 API",
        f"/api/v1/overview -> {code}（期望 401）")

    opener, error = http_login()
    if not opener:
        add(results, "FAIL", "登录", error)
        return results

    code, body = http_open("/api/v1/overview", opener=opener)
    add(results, "PASS" if code == 200 else "FAIL", "登录后 API",
        f"/api/v1/overview -> {code}")
    if code == 200:
        try:
            data = json.loads(body)
            info = f"keys={list(data)[:4]}"
            add(results, "INFO", "overview 返回", info)
        except Exception:
            add(results, "WARN", "overview JSON", "返回不是合法 JSON")

    code, _ = http_open("/docs")
    add(results, "PASS" if code in (401, 404) else "WARN", "OpenAPI 文档",
        f"/docs -> {code}（默认应关闭）")
    return results


def check_collection() -> list:
    results = []
    title("3. 真实采集与证据链")
    conn = db_conn()
    if conn is None:
        add(results, "FAIL", "数据库", f"无法读取 {DB}")
        return results
    try:
        runs = scalar(conn, "SELECT COUNT(*) FROM collection_runs")
        obs = scalar(conn, "SELECT COUNT(*) FROM state_observations")
        add(results, "PASS" if runs >= 1 else "FAIL", "采集批次", str(runs))
        add(results, "PASS" if obs >= 100 else "FAIL", "状态观测", str(obs))

        latest = conn.execute(
            "SELECT run_id, summary_json, started_at_utc, published_at_utc "
            "FROM collection_runs ORDER BY started_at_utc DESC LIMIT 1"
        ).fetchone()
        if not latest:
            add(results, "FAIL", "最新批次", "没有采集批次")
        else:
            summary = json.loads(latest["summary_json"] or "{}")
            add(results, "PASS", "最新批次", latest["run_id"])
            ssh_ok = summary.get("ssh_commands_success", 0)
            ssh_fail = summary.get("ssh_commands_failed", 0)
            rate = ssh_ok / (ssh_ok + ssh_fail) if (ssh_ok + ssh_fail) else 0
            add(results, "PASS" if rate >= 0.95 else "FAIL", "SSH 成功率",
                f"{rate * 100:.1f}%（{ssh_ok}/{ssh_ok + ssh_fail}）")
            conn_tests = summary.get("connectivity_tests", 0)
            conn_ok = summary.get("connectivity_successes", 0)
            add(results, "PASS" if conn_tests and conn_ok == conn_tests else "WARN",
                "连通性测试", f"{conn_ok}/{conn_tests}")
            age = parse_dt(latest["started_at_utc"])
            if age:
                hours = (datetime.now(timezone.utc) - age).total_seconds() / 3600
                add(results, "PASS" if hours <= 0.5 else "WARN", "最新批次时效",
                    f"{hours:.1f} 小时（目标 <=0.5；验收前建议重新采集）")

        missing_time = scalar(
            conn, "SELECT COUNT(*) FROM state_observations WHERE observed_at_utc IS NULL"
        )
        add(results, "PASS" if missing_time == 0 else "FAIL", "观测时间完整性",
            f"缺失 {missing_time} 条")

        unpublished = conn.execute(
            "SELECT run_id, status, started_at_utc FROM collection_runs "
            "WHERE published_at_utc IS NULL ORDER BY started_at_utc DESC"
        ).fetchall()
        if unpublished:
            detail = ", ".join(
                f"{r['run_id']}({r['status']})" for r in unpublished[:5]
            )
            add(results, "FAIL", "未发布批次", detail)
        else:
            add(results, "PASS", "未发布批次", "0")
    finally:
        conn.close()
    return results


def check_knowledge() -> list:
    results = []
    title("4. 知识库")
    raw = json.loads((ROOT / "knowledge_base" / "articles.json").read_text())
    articles = raw.get("articles", raw) if isinstance(raw, dict) else raw
    categories = sorted({a.get("category") for a in articles})
    missing_url = sum(1 for a in articles if not a.get("source_url"))
    add(results, "PASS" if len(articles) >= 20 else "FAIL", "文章数量",
        f"{len(articles)}（目标 >=20）")
    add(results, "PASS" if len(categories) >= 5 else "WARN", "类别覆盖",
        ", ".join(categories))
    add(results, "WARN" if missing_url else "PASS", "source_url 覆盖",
        f"{len(articles) - missing_url}/{len(articles)}")

    conn = db_conn()
    if conn is None:
        add(results, "FAIL", "知识库表", f"无法读取 {DB}")
        return results
    try:
        count = scalar(conn, "SELECT COUNT(*) FROM knowledge_articles")
        active = conn.execute(
            "SELECT index_version_id, article_count, status FROM knowledge_index_versions "
            "WHERE is_active=1 ORDER BY created_at_utc DESC LIMIT 1"
        ).fetchone()
        add(results, "PASS" if count >= 20 else "FAIL", "关系表文章数", str(count))
        if active:
            status_ok = active["status"] in ("active", "ready", "published")
            add(results, "PASS" if status_ok else "WARN", "活动索引",
                f"{active['index_version_id']} / {active['article_count']} 篇 / status={active['status']}")
        else:
            add(results, "WARN", "活动索引", "没有 active 索引")
    finally:
        conn.close()

    if service_running():
        opener, error = http_login()
        if opener:
            code, body = http_open("/api/v1/knowledge-base/status", opener=opener)
            add(results, "PASS" if code == 200 else "WARN", "知识库 API",
                f"/api/v1/knowledge-base/status -> {code}")
    else:
        add(results, "WARN", "知识库 API", "Web 未启动，跳过")
    return results


def check_diagnosis(interactive: bool = True) -> list:
    results = []
    title("5. RAG 诊断")
    conn = db_conn()
    if conn is None:
        add(results, "FAIL", "数据库", f"无法读取 {DB}")
        return results
    try:
        diagnoses = scalar(conn, "SELECT COUNT(*) FROM diagnosis_records")
        calls = scalar(conn, "SELECT COUNT(*) FROM model_calls")
        add(results, "PASS" if diagnoses >= 1 else "FAIL", "诊断记录", str(diagnoses))
        add(results, "PASS" if calls >= 1 else "FAIL", "模型调用记录", str(calls))

        rows = conn.execute(
            "SELECT id, use_kb, model_id, response_time_ms, total_response_time_ms, "
            "json_array_length(evidence_json) AS evidence_count, "
            "json_array_length(retrieved_doc_ids_json) AS doc_count, created_at "
            "FROM diagnosis_records ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if rows:
            add(results, "INFO", "最近诊断",
                "; ".join(
                    f"#{r['id']} kb={r['use_kb']} ev={r['evidence_count']} docs={r['doc_count']}"
                    for r in rows[:3]
                ))
        statuses = conn.execute(
            "SELECT status, COUNT(*) AS c FROM model_calls GROUP BY status"
        ).fetchall()
        add(results, "PASS" if any(r["status"] == "success" for r in statuses) else "WARN",
            "模型调用状态", ", ".join(f"{r['status']}={r['c']}" for r in statuses))
    finally:
        conn.close()

    if not service_running():
        add(results, "WARN", "实时诊断", "Web 未启动，跳过")
        return results
    if interactive:
        answer = input("  是否发起一次真实诊断（会调用 LLM，可能产生费用）？[y/N] ").strip().lower()
        if answer != "y":
            add(results, "INFO", "实时诊断", "已跳过")
            return results
    else:
        add(results, "INFO", "实时诊断", "全量验收不自动调用 LLM")
        return results

    opener, error = http_login()
    if not opener:
        add(results, "FAIL", "实时诊断登录", error)
        return results
    code, payload = http_post_json(
        opener,
        "/api/v1/diagnose",
        {
            "question": "帮我检查一下总部和分支A之间的网络现在是否正常。",
            "use_kb": True,
            "requested_by": "acceptance-menu",
        },
        timeout=180,
    )
    if code == 200 and isinstance(payload, dict):
        answer_data = payload.get("answer") or {}
        fields = [k for k in (
            "fault_phenomenon", "evidence_used", "possible_causes",
            "check_steps", "fix_suggestions", "recheck_method",
        ) if answer_data.get(k)]
        add(results, "PASS", "实时诊断",
            f"diagnosis_id={payload.get('diagnosis_id')} 字段={len(fields)}/6")
    else:
        add(results, "FAIL", "实时诊断", f"HTTP {code}: {str(payload)[:200]}")
    return results


def check_faults() -> list:
    results = []
    title("6. 故障注入、修复与复测（只读）")
    conn = db_conn()
    if conn is None:
        add(results, "FAIL", "数据库", f"无法读取 {DB}")
        return results
    try:
        injections = conn.execute(
            "SELECT fault_type, COUNT(*) AS c FROM fault_injections "
            "WHERE injection_mode='live' GROUP BY fault_type ORDER BY fault_type"
        ).fetchall()
        total = sum(r["c"] for r in injections)
        add(results, "PASS" if total >= 8 else "WARN", "真实故障注入",
            f"{total} 次；" + ", ".join(f"{r['fault_type']}={r['c']}" for r in injections))
        manual = conn.execute(
            "SELECT status, COUNT(*) AS c FROM manual_actions GROUP BY status"
        ).fetchall()
        add(results, "PASS" if manual else "WARN", "人工处理记录",
            ", ".join(f"{r['status']}={r['c']}" for r in manual) or "0")
        sessions = scalar(conn, "SELECT COUNT(*) FROM terminal_sessions")
        add(results, "PASS" if sessions >= 1 else "WARN", "审计终端会话", str(sessions))
    finally:
        conn.close()

    print()
    print(c("  四类故障演练命令（人工执行，脚本不会自动下发）：", BOLD))
    print("""
  链路 down（BR-A-R2 Ethernet0/0）
    configure terminal
    interface Ethernet0/0
     shutdown
    end
    恢复：interface Ethernet0/0 / no shutdown

  VLAN 错配（BR-A-SW2 Ethernet1/0）
    configure terminal
    interface Ethernet1/0
     switchport access vlan 20
    end
    恢复：switchport access vlan 10

  默认路由缺失（BR-A-R2）
    configure terminal
    no ip route 0.0.0.0 0.0.0.0 172.16.1.1
    end
    恢复：ip route 0.0.0.0 0.0.0.0 172.16.1.1

  ACL 过度封锁（HQ-R1 Ethernet1/0）
    configure terminal
    ip access-list extended PILOT-BLOCK-A
     deny ip 10.20.10.0 0.0.0.255 10.10.10.0 0.0.0.255
     permit ip any any
    end
    configure terminal
    interface Ethernet1/0
     no ip access-group WAN-IN in
     ip access-group PILOT-BLOCK-A in
    end
    恢复：删除 PILOT-BLOCK-A，重新应用 WAN-IN
""")
    print(c("  注意：所有写操作必须先备份，并通过审计终端的一次性令牌确认。", YELLOW))
    return results


def fix_unpublished() -> list:
    """Mark stale (older than 30 min) running batches as failed and publish them."""
    results = []
    title("12. 清理未发布的 running 批次（写数据库）")
    conn = db_conn_rw()
    if conn is None:
        add(results, "FAIL", "数据库", f"无法写入 {DB}")
        return results
    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=30)
        ).isoformat(timespec="seconds")
        stale = conn.execute(
            "SELECT run_id, started_at_utc FROM collection_runs "
            "WHERE published_at_utc IS NULL AND status='running' "
            "AND started_at_utc < ? ORDER BY started_at_utc",
            (cutoff,),
        ).fetchall()
        fresh = conn.execute(
            "SELECT run_id, started_at_utc FROM collection_runs "
            "WHERE published_at_utc IS NULL AND status='running' "
            "AND started_at_utc >= ? ORDER BY started_at_utc",
            (cutoff,),
        ).fetchall()
        if fresh:
            add(results, "WARN", "仍在运行的批次",
                ", ".join(r["run_id"] for r in fresh) + "（30 分钟内，未处理）")
        if not stale:
            add(results, "PASS", "可清理批次", "没有超过 30 分钟的 running 批次")
            return results

        for row in stale:
            print(f"  - {row['run_id']}  started={row['started_at_utc']}")
        answer = input("  确认把这些批次标记为 failed 并发布？[y/N] ").strip().lower()
        if answer != "y":
            add(results, "INFO", "清理", "已取消")
            return results

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for row in stale:
            conn.execute(
                "UPDATE collection_runs "
                "SET status='failed', finished_at_utc=COALESCE(finished_at_utc, ?), "
                "published_at_utc=COALESCE(published_at_utc, ?) "
                "WHERE run_id=? AND published_at_utc IS NULL AND status='running'",
                (now, now, row["run_id"]),
            )
        conn.commit()
        add(results, "PASS", "已清理", f"{len(stale)} 个批次标记为 failed 并发布")
        add(results, "INFO", "下一步", "重新运行第 7 项验收审计，C6 应变为 PASS")
    finally:
        conn.close()
    return results


def check_evaluation() -> list:
    results = []
    title("7. 评测与验收审计")
    code, output = run(
        [PY, ROOT / "scripts" / "verify_acceptance.py",
         "--db", DB, "--json-output", AUDIT_JSON],
        timeout=180,
    )
    print(output)
    if code == 0:
        add(results, "PASS", "verify_acceptance.py", "exit=0")
    else:
        add(results, "FAIL", "verify_acceptance.py",
            f"exit={code}（查看上方 FAIL 项）")

    if AUDIT_JSON.exists():
        try:
            payload = json.loads(AUDIT_JSON.read_text())
            checks = payload.get("checks", [])
            counts = payload.get("summary", {}).get("check_status", {})
            add(results, "INFO", "审计汇总",
                ", ".join(f"{k}={v}" for k, v in counts.items()))
            for item in checks:
                if item.get("id") in ("E2-results", "E3-cases", "E4-fault-injections"):
                    add(results, item.get("status", "WARN"),
                        item.get("id", ""), f"{item.get('value')} / 目标 {item.get('target')}")
        except Exception as exc:
            add(results, "WARN", "审计 JSON", f"解析失败: {exc}")
    return results


def check_security() -> list:
    results = []
    title("8. 安全与审计")
    tests = [
        "tests/test_whitelist.py",
        "tests/test_write_policy.py",
        "tests/test_write_confirmations.py",
        "tests/test_terminal_execution_security.py",
        "tests/test_sanitize.py",
        "tests/test_security.py",
    ]
    code, output = run([PY, "-m", "pytest", "-q", *tests], timeout=300)
    add(results, "PASS" if code == 0 else "FAIL", "安全回归测试",
        "passed" if code == 0 else (output.splitlines()[-1] if output else f"exit={code}"))

    code, _ = run(["git", "ls-files", "--error-unmatch", ".env"], timeout=30)
    env_tracked = code == 0
    add(results, "PASS" if not env_tracked else "FAIL", ".env 未跟踪",
        "未跟踪" if not env_tracked else "警告：.env 已被 Git 跟踪；立即轮换所有凭据")

    code, output = run(["git", "check-ignore", "-v", ".env"], timeout=30)
    add(results, "PASS" if code == 0 else "FAIL", ".env 已忽略",
        output.strip().splitlines()[0] if output.strip()
        else "已跟踪文件不受 .gitignore 约束")

    code, output = run(["git", "ls-files", ".env", ".env.*", "reports/backups/*.cfg"], timeout=30)
    secret_files = [
        item.strip() for item in output.splitlines()
        if item.strip() and item.strip() != ".env.example"
    ]
    add(results, "PASS" if not secret_files else "FAIL", "含凭据文件",
        "0" if not secret_files else f"{len(secret_files)} 个已跟踪（如 {', '.join(secret_files[:3])}）")

    code, output = run(
        ["grep", "-rn", "send_config_set\\|terminal_manager.execute", "rag_engine/"],
        timeout=30,
    )
    add(results, "PASS" if code != 0 else "WARN", "模型直写设备路径",
        "未发现" if code != 0 else "发现直接下发代码，请人工复核")

    conn = db_conn()
    if conn:
        try:
            audit = scalar(conn, "SELECT COUNT(*) FROM audit_log")
            events = scalar(conn, "SELECT COUNT(*) FROM terminal_events")
            add(results, "PASS" if audit else "WARN", "审计事件", str(audit))
            add(results, "PASS" if events else "WARN", "终端事件", str(events))
        finally:
            conn.close()
    return results


def check_web() -> list:
    results = []
    title("9. Web 展示与报告")
    if not service_running():
        add(results, "WARN", "Web 服务", "未启动；先运行 start.py --live")
    else:
        opener, error = http_login()
        if not opener:
            add(results, "FAIL", "登录", error)
        else:
            add(results, "PASS", "登录", "200")
            for path in (
                "/console/overview", "/console/assets", "/console/diagnosis",
                "/console/knowledge", "/console/settings",
            ):
                code, _ = http_open(path, opener=opener)
                add(results, "PASS" if code == 200 else "FAIL", f"页面 {path}",
                    str(code))

    md = len(list((ROOT / "reports").glob("diagnosis_*.md")))
    html = len(list((ROOT / "reports").glob("diagnosis_*.html")))
    backups = len(list((ROOT / "reports" / "backups").glob("*.cfg")))
    figures = len(list((ROOT / "pic").glob("fig*.png")))
    add(results, "PASS" if md else "WARN", "Markdown 报告", str(md))
    add(results, "PASS" if html else "WARN", "HTML 报告", str(html))
    add(results, "PASS" if backups else "WARN", "配置备份", str(backups))
    add(results, "PASS" if figures else "WARN", "配图", str(figures))
    return results


def check_tests() -> list:
    results = []
    title("10. 后端测试 + 静态检查 + 前端构建")
    code, output = run([PY, "-m", "pytest", "-q"], timeout=900)
    last = [line_text for line_text in output.splitlines() if line_text.strip()][-1] if output.strip() else ""
    add(results, "PASS" if code == 0 else "FAIL", "pytest", last or f"exit={code}")

    code, output = run([PY, "-m", "ruff", "check", "."], timeout=180)
    add(results, "PASS" if code == 0 else "FAIL", "ruff",
        "All checks passed" if code == 0 else (output.strip()[-200:] or f"exit={code}"))

    npm = shutil.which("npm")
    if not npm:
        add(results, "WARN", "前端", "npm 未安装，跳过")
        return results

    answer = input("  是否继续运行前端 npm test + npm run build？[y/N] ").strip().lower()
    if answer != "y":
        add(results, "INFO", "前端", "已跳过")
        return results

    code, output = run([npm, "test"], cwd=FRONTEND, timeout=600)
    add(results, "PASS" if code == 0 else "FAIL", "npm test",
        "passed" if code == 0 else (output.strip()[-200:] or f"exit={code}"))
    code, output = run([npm, "run", "build"], cwd=FRONTEND, timeout=600)
    add(results, "PASS" if code == 0 else "FAIL", "npm run build",
        "success" if code == 0 else (output.strip()[-200:] or f"exit={code}"))
    return results


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------
CHECKS = {
    "1": ("环境与网络接入", check_environment),
    "2": ("启动、登录与鉴权", check_startup),
    "3": ("真实采集与证据链", check_collection),
    "4": ("知识库", check_knowledge),
    "5": ("RAG 诊断", check_diagnosis),
    "6": ("故障注入/修复记录（只读）", check_faults),
    "7": ("评测与验收审计", check_evaluation),
    "8": ("安全与审计", check_security),
    "9": ("Web 展示与报告", check_web),
    "10": ("后端测试 + 前端构建", check_tests),
}


def run_all() -> None:
    title("运行全部只读验收")
    all_results = []
    all_results += check_environment()
    all_results += check_startup()
    all_results += check_collection()
    all_results += check_knowledge()
    all_results += check_diagnosis(interactive=False)
    all_results += check_faults()
    all_results += check_evaluation()
    all_results += check_security()
    all_results += check_web()
    title("总验收结果")
    show(all_results)


def menu() -> None:
    while True:
        line()
        print(c(" NetOps Console 一键验收菜单", BOLD))
        line()
        for key, (name, _) in CHECKS.items():
            print(f"  {key:>2}. {name}")
        print("  11. 运行全部只读验收")
        print("  12. 清理未发布的 running 批次（写数据库，需确认）")
        print("   0. 退出")
        line("-")
        choice = input("请输入编号: ").strip()
        print()
        if choice == "0":
            print("已退出。")
            return
        if choice == "11":
            run_all()
        elif choice == "12":
            show(fix_unpublished())
        elif choice in CHECKS:
            name, func = CHECKS[choice]
            if choice == "5":
                results = func(interactive=True)
            elif choice == "10":
                results = func()
            else:
                results = func()
            show(results)
        else:
            print(c("  无效编号，请重新输入。", RED))
            continue
        input("\n按 Enter 返回菜单...")
        print()


def main() -> int:
    try:
        menu()
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
