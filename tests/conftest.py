import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 测试隔离：临时目录放在项目根目录下，避免受限执行环境的系统临时目录不可写。
_TMP_OBJ = tempfile.TemporaryDirectory(
    prefix=".netops_test_",
    dir=str(PROJECT_ROOT),
    ignore_cleanup_errors=True,
)
_TMP_DIR = _TMP_OBJ.name
os.environ["APP_API_TOKEN"] = "test-token-0123456789"
os.environ["LLM_API_KEY"] = "dummy"
os.environ["APP_AUTO_COLLECT"] = "false"
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP_DIR, "test_ops.db")
os.environ["CHROMA_DB_PATH"] = os.path.join(_TMP_DIR, "chroma")
