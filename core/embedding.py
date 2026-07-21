"""BGE-M3 本地 encoder。仅组合根（scripts/run_cli.py、scripts/init_db.py、api/）import 本模块；
core.retrieval 只依赖 Encoder 协议，单元测试因此无需加载真实模型（~2GB）。"""

import structlog
from sentence_transformers import SentenceTransformer

logger = structlog.get_logger()


class BGEEncoder:
    """把 SentenceTransformer 适配为 retrieval.Encoder 协议（返回纯 list[float]）。

    local_files_only=True 跳过 Hub 联网检查（模型已缓存在本地时启动更快、离线可用）；
    首次下载模型时用 False（init_db 默认）。"""

    def __init__(self, cache_folder: str, model_name: str = "BAAI/bge-m3", local_files_only: bool = False):
        logger.info("encoder_loading", model=model_name, local_files_only=local_files_only,
                    note="约 2GB，CPU 加载约 30 秒")
        self._model = SentenceTransformer(
            model_name, cache_folder=cache_folder, local_files_only=local_files_only
        )
        # sentence-transformers ≥3.x 改名 get_embedding_dimension，保留旧名回退
        get_dim = getattr(self._model, "get_embedding_dimension", None) or getattr(
            self._model, "get_sentence_embedding_dimension"
        )
        dim = get_dim()
        if dim is None:
            raise RuntimeError(f"无法确定模型 {model_name} 的向量维度")
        self._dim = dim
        logger.info("encoder_ready", model=model_name, dim=dim)

    def encode(self, text: str, normalize_embeddings: bool = True) -> list[float]:
        return self._model.encode(text, normalize_embeddings=normalize_embeddings).tolist()

    def encode_batch(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=normalize_embeddings).tolist()

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim
