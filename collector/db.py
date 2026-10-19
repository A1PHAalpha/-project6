"""
SQLite 数据层：设备台账缓存、采集记录、诊断记录、人工处理记录和运行状态。
所有时间统一用 ISO8601 字符串存储，便于溯源。
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from project_config import load_project_env

load_project_env()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = os.getenv("SQLITE_DB_PATH") or str(PROJECT_ROOT / "data" / "ops.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_name TEXT PRIMARY KEY,
    role TEXT,
    site TEXT,
    vendor TEXT,
    model TEXT,
    mgmt_ip TEXT,
    device_type TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS collection_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL,
    collected_at TEXT NOT NULL,       -- ISO8601 采集时间
    status TEXT NOT NULL,             -- success / failed
    fail_reason TEXT,                 -- 失败原因（status=failed 时）
    command TEXT,                     -- 执行的命令
    raw_output TEXT,                  -- 原始输出
    parsed_json TEXT,                 -- 结构化解析结果
    FOREIGN KEY (device_name) REFERENCES devices(device_name)
);

CREATE INDEX IF NOT EXISTS idx_collection_device_time
    ON collection_records(device_name, collected_at DESC);

CREATE TABLE IF NOT EXISTS diagnosis_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_template_id TEXT NOT NULL,
    llm_params_json TEXT,              -- temperature 等参数
    retrieved_doc_ids_json TEXT,       -- 本次检索到的知识库条目ID列表
    evidence_json TEXT,                -- 本次引用的设备采集证据(含设备名+采集时间)
    answer_json TEXT NOT NULL,         -- 结构化诊断结果
    use_kb INTEGER NOT NULL DEFAULT 1, -- 1=启用检索, 0=禁用检索(对比测试用)
    response_time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS manual_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id INTEGER,
    created_at TEXT NOT NULL,
    operator TEXT,
    action_desc TEXT,                 -- 人工执行的操作描述
    backup_ref TEXT,                  -- 变更前备份文件引用
    recheck_result TEXT,              -- 复测结果
    status TEXT,                      -- confirmed / rejected / completed
    FOREIGN KEY (diagnosis_id) REFERENCES diagnosis_records(id)
);

CREATE TABLE IF NOT EXISTS system_status (
    status_key TEXT PRIMARY KEY,
    status_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_device(device: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO devices (device_name, role, site, vendor, model, mgmt_ip, device_type, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_name) DO UPDATE SET
                 role=excluded.role, site=excluded.site, vendor=excluded.vendor,
                 model=excluded.model, mgmt_ip=excluded.mgmt_ip,
                 device_type=excluded.device_type, raw_json=excluded.raw_json""",
            (
                device["device_name"], device.get("role"), device.get("site"),
                device.get("vendor"), device.get("model"), device.get("mgmt_ip"),
                device.get("device_type"), json.dumps(device, ensure_ascii=False),
            ),
        )


def load_inventory(json_path: str):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for dev in data["devices"]:
        upsert_device(dev)
    return data["devices"]


def insert_collection_record(device_name, status, command=None, raw_output=None,
                              parsed=None, fail_reason=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO collection_records
               (device_name, collected_at, status, fail_reason, command, raw_output, parsed_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device_name, now_iso(), status, fail_reason, command, raw_output,
             json.dumps(parsed, ensure_ascii=False) if parsed is not None else None),
        )


def get_latest_record(device_name: str, command: str = None):
    """获取某设备最近一次采集记录（可选按命令过滤），不存在则返回 None。"""
    with get_conn() as conn:
        if command:
            row = conn.execute(
                """SELECT * FROM collection_records
                   WHERE device_name=? AND command=?
                   ORDER BY collected_at DESC LIMIT 1""",
                (device_name, command),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM collection_records
                   WHERE device_name=?
                   ORDER BY collected_at DESC LIMIT 1""",
                (device_name,),
            ).fetchone()
        return dict(row) if row else None


def get_all_latest_status():
    """获取所有设备各命令类型的最新采集记录，用于 Web 总览页。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT cr.* FROM collection_records cr
               WHERE cr.id IN (
                   SELECT MAX(id) FROM collection_records GROUP BY device_name, command
               )
               ORDER BY cr.device_name, cr.command"""
        ).fetchall()
        return [dict(r) for r in rows]


def list_devices():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM devices").fetchall()
        return [dict(r) for r in rows]


def insert_diagnosis(question, model_id, prompt_template_id, llm_params,
                      retrieved_doc_ids, evidence, answer, use_kb=1, response_time_ms=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO diagnosis_records
               (created_at, question, model_id, prompt_template_id, llm_params_json,
                retrieved_doc_ids_json, evidence_json, answer_json, use_kb, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), question, model_id, prompt_template_id,
             json.dumps(llm_params, ensure_ascii=False),
             json.dumps(retrieved_doc_ids, ensure_ascii=False),
             json.dumps(evidence, ensure_ascii=False),
             json.dumps(answer, ensure_ascii=False),
             use_kb, response_time_ms),
        )
        return cur.lastrowid


def list_diagnoses(limit=50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM diagnosis_records ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_diagnosis(diagnosis_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM diagnosis_records WHERE id=?", (diagnosis_id,)
        ).fetchone()
        return dict(row) if row else None


def insert_manual_action(diagnosis_id, operator, action_desc, backup_ref,
                          recheck_result, status):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO manual_actions
               (diagnosis_id, created_at, operator, action_desc, backup_ref, recheck_result, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (diagnosis_id, now_iso(), operator, action_desc, backup_ref, recheck_result, status),
        )
        return cur.lastrowid


def list_manual_actions(diagnosis_id=None):
    with get_conn() as conn:
        if diagnosis_id:
            rows = conn.execute(
                "SELECT * FROM manual_actions WHERE diagnosis_id=? ORDER BY id DESC",
                (diagnosis_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM manual_actions ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def set_system_status(status_key: str, status: dict):
    """Insert or replace a runtime status record."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO system_status (status_key, status_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(status_key) DO UPDATE SET
                 status_json=excluded.status_json,
                 updated_at=excluded.updated_at""",
            (status_key, json.dumps(status, ensure_ascii=False), now_iso()),
        )


def get_system_status(status_key: str):
    """Return one runtime status record as a dict, or None when absent."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status_json FROM system_status WHERE status_key=?",
            (status_key,),
        ).fetchone()
    return json.loads(row["status_json"]) if row else None


def list_system_status():
    """Return all runtime status records keyed by status name."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status_key, status_json, updated_at FROM system_status ORDER BY status_key"
        ).fetchall()
    return {
        row["status_key"]: {
            "updated_at": row["updated_at"],
            **json.loads(row["status_json"]),
        }
        for row in rows
    }
