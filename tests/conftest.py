import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 测试隔离：使用临时数据库/向量库，且固定测试用凭据（避免依赖开发者本地 .env）
_TMP_DIR = tempfile.mkdtemp(prefix="netops_test_")
os.environ["APP_API_TOKEN"] = "test-token-0123456789"
os.environ["LLM_API_KEY"] = "dummy"
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP_DIR, "test_ops.db")
os.environ["CHROMA_DB_PATH"] = os.path.join(_TMP_DIR, "chroma")
