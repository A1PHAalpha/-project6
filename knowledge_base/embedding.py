"""本地稠密向量化：fastembed(ONNX) + BAAI/bge-small-zh-v1.5。

与旧 sentence-transformers 方案使用同一份模型权重，检索语义不变，
但去掉 PyTorch 重依赖（venv 体积、CI 拉包时间大幅下降）。

说明：chromadb 1.5.x 已移除内置稠密 FastEmbedEmbeddingFunction，
因此这里按 chromadb 的 EmbeddingFunction Protocol 自行封装。
"""
import os
from typing import Any, Dict

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from chromadb.utils.embedding_functions import register_embedding_function
from fastembed import TextEmbedding

from project_config import load_project_env

load_project_env()

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
CACHE_DIR = os.getenv("FASTEMBED_CACHE_PATH")  # 可选：把模型缓存固定到仓库外/内


@register_embedding_function
class FastEmbedEmbeddingFunction(EmbeddingFunction[Documents]):
    def __init__(self, model_name: str = MODEL_NAME, cache_dir: str | None = CACHE_DIR) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None  # 懒加载，import 阶段不下载模型

    @property
    def model(self) -> TextEmbedding:
        if self._model is None:
            kwargs: Dict[str, Any] = {"model_name": self.model_name}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            self._model = TextEmbedding(**kwargs)
        return self._model

    def __call__(self, input: Documents) -> Embeddings:
        return [vec.tolist() for vec in self.model.embed(list(input))]

    @classmethod
    def name(cls) -> str:
        # chromadb 注册表要求 name() 为 classmethod
        return f"fastembed-{MODEL_NAME}"

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "FastEmbedEmbeddingFunction":
        return FastEmbedEmbeddingFunction(**config)

    def get_config(self) -> Dict[str, Any]:
        return {"model_name": self.model_name, "cache_dir": self.cache_dir}
