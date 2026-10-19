"""Application startup preparation and readiness checks.

Starting the web app should be sufficient for local development:

- initialize SQLite and seed the device inventory when the database is empty;
- inspect the local knowledge base without blocking startup;
- persist the latest database and knowledge-base status in SQLite.

Knowledge-base initialization is intentionally not triggered here yet. The
status is recorded so the Web app can expose manual build/rebuild workflows.
"""
import logging
import threading

from collector import db
from collector.run_collect import CollectionConfigurationError, run_collection
from knowledge_base.build_index import (
    CHROMA_PATH,
    COLLECTION_NAME,
    get_chroma_client,
    get_embedding_function,
)
from knowledge_base.embedding import MODEL_NAME
from project_config import PROJECT_ROOT, env_flag

logger = logging.getLogger("app_bootstrap")

INVENTORY_PATH = PROJECT_ROOT / "network" / "device_inventory.json"
DATABASE_STATUS_KEY = "database"
KNOWLEDGE_BASE_STATUS_KEY = "knowledge_base"
RUNTIME_STATUS_KEY = "runtime"
COLLECTION_STATUS_KEY = "startup_collection"

_collection_thread = None
_collection_thread_lock = threading.Lock()


def ensure_database() -> dict:
    """Initialize SQLite and synchronize the device inventory into a new version."""
    db.init_db()
    devices = db.list_devices()
    inventory_loaded = False
    warning = None

    if INVENTORY_PATH.exists():
        try:
            inventory = db.load_inventory_with_version(str(INVENTORY_PATH))
            devices = db.list_devices()
            inventory_loaded = True
        except Exception as exc:
            logger.exception("设备台账加载失败")
            warning = f"设备台账加载失败: {type(exc).__name__}: {exc}"
    else:
        warning = f"设备台账不存在: {INVENTORY_PATH}"

    status = {
        "status": "ready" if not warning else "degraded",
        "device_count": len(devices),
        "inventory_loaded": inventory_loaded,
        "inventory_version_id": inventory.get("inventory_version_id") if inventory_loaded else None,
        "db_path": db.DB_PATH,
        "warning": warning,
    }
    db.set_system_status(DATABASE_STATUS_KEY, status)
    return status


def inspect_knowledge_base(
    chroma_path: str | None = None,
    collection_name: str | None = None,
) -> dict:
    """Inspect the persisted Chroma collection without loading the model."""
    target_path = chroma_path or CHROMA_PATH
    target_collection = collection_name or COLLECTION_NAME
    status = {
        "status": "missing",
        "chroma_path": str(target_path),
        "collection": target_collection,
        "model": MODEL_NAME,
        "count": 0,
        "message": "",
        "checked_at": db.now_iso(),
    }

    try:
        client = get_chroma_client(target_path)
        names = {collection.name for collection in client.list_collections()}
        if target_collection not in names:
            status["message"] = "尚未初始化知识库索引"
            return status

        collection = client.get_collection(
            name=target_collection,
            embedding_function=get_embedding_function(),
        )
        count = collection.count()
        status["count"] = count
        if count > 0:
            status["status"] = "ready"
            status["message"] = f"知识库可用，共 {count} 条资料"
        else:
            status["status"] = "empty"
            status["message"] = "知识库集合存在，但没有任何资料"
    except Exception as exc:
        logger.exception("知识库状态检查失败")
        status["status"] = "error"
        status["message"] = f"知识库状态检查失败: {type(exc).__name__}: {exc}"
    return status


def run_startup_collection_once(devices_arg: str | None = None) -> dict:
    """Run one collection pass synchronously and persist its outcome."""
    db.init_db()
    status = {
        "status": "running",
        "started_at": db.now_iso(),
        "finished_at": None,
        "message": "启动采集正在执行",
    }
    db.set_system_status(COLLECTION_STATUS_KEY, status)

    try:
        result = run_collection(
            devices_arg,
            trigger_type="startup",
            requested_by="application_startup",
        )
    except CollectionConfigurationError as exc:
        status.update({
            "status": "skipped",
            "finished_at": db.now_iso(),
            "message": str(exc),
        })
    except Exception as exc:
        logger.exception("启动采集失败")
        status.update({
            "status": "failed",
            "finished_at": db.now_iso(),
            "message": f"启动采集失败: {type(exc).__name__}: {exc}",
        })
    else:
        evidence_success = (
            result["ssh_commands_success"]
            + result["snmp_devices_success"]
            + result.get("connectivity_successes", 0)
        )
        evidence_failed = (
            result["ssh_commands_failed"]
            + result["snmp_devices_failed"]
            + result.get("connectivity_failures", 0)
            + len(result["missing_devices"])
        )
        if evidence_success == 0:
            collection_state = "failed"
            message = "启动采集未获得任何有效设备数据"
        elif evidence_failed:
            collection_state = "partial"
            message = "启动采集完成，但存在部分设备或命令失败"
        else:
            collection_state = "success"
            message = "启动采集完成"
        status.update({
            **result,
            "status": collection_state,
            "finished_at": db.now_iso(),
            "message": message,
        })

    db.set_system_status(COLLECTION_STATUS_KEY, status)
    log_method = logger.info if status["status"] in ("success", "partial") else logger.warning
    log_method("%s: %s", status["message"], status["status"])
    return status


def start_startup_collection() -> dict:
    """Start one non-blocking startup collection unless disabled or already running."""
    global _collection_thread

    db.init_db()
    if not env_flag("APP_AUTO_COLLECT", True):
        status = {
            "status": "disabled",
            "started_at": None,
            "finished_at": db.now_iso(),
            "message": "APP_AUTO_COLLECT=false，已跳过启动采集",
        }
        db.set_system_status(COLLECTION_STATUS_KEY, status)
        return status

    with _collection_thread_lock:
        if _collection_thread and _collection_thread.is_alive():
            return db.get_system_status(COLLECTION_STATUS_KEY) or {
                "status": "running",
                "message": "启动采集正在执行",
            }

        status = {
            "status": "running",
            "started_at": db.now_iso(),
            "finished_at": None,
            "message": "启动采集已开始，正在后台执行",
        }
        db.set_system_status(COLLECTION_STATUS_KEY, status)
        _collection_thread = threading.Thread(
            target=run_startup_collection_once,
            name="startup-collection",
            daemon=True,
        )
        _collection_thread.start()
    logger.info("已启动后台采集任务")
    return status


def bootstrap_runtime() -> dict:
    """Prepare local state and return the latest runtime readiness snapshot."""
    database_status = ensure_database()
    knowledge_base_status = inspect_knowledge_base()

    db.set_system_status(KNOWLEDGE_BASE_STATUS_KEY, knowledge_base_status)
    collection_status = start_startup_collection()
    degraded = (
        database_status["status"] != "ready"
        or knowledge_base_status["status"] != "ready"
    )
    runtime_status = {
        "status": "degraded" if degraded else "ready",
        "checked_at": db.now_iso(),
        "database": database_status,
        "knowledge_base": knowledge_base_status,
        "startup_collection": collection_status,
    }

    db.set_system_status(RUNTIME_STATUS_KEY, runtime_status)

    if knowledge_base_status["status"] == "ready":
        logger.info(knowledge_base_status["message"])
    else:
        logger.warning(
            "%s；服务继续启动，诊断将降级为仅使用设备证据",
            knowledge_base_status["message"],
        )
    return runtime_status
