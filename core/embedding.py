"""BGE-M3 本地 encoder。仅组合根（scripts/run_cli.py、scripts/init_db.py、api/）import 本模块；
core.retrieval 只依赖 Encoder 协议，单元测试因此无需加载真实模型（~2GB）。"""

from sentence_transformers import SentenceTransformer


class BGEEncoder:
    """把 SentenceTransformer 适配为 retrieval.Encoder 协议（返回纯 list[float]）。"""

    def __init__(self, cache_folder: str, model_name: str = "BAAI/bge-m3"):
        self._model = SentenceTransformer(model_name, cache_folder=cache_folder)
        dim = self._model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(f"无法确定模型 {model_name} 的向量维度")
        self._dim = dim

    def encode(self, text: str, normalize_embeddings: bool = True) -> list[float]:
        return self._model.encode(text, normalize_embeddings=normalize_embeddings).tolist()

    def encode_batch(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=normalize_embeddings).tolist()

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim
