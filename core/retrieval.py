"""混合检索：FTS5 关键词 + sqlite-vec 向量 + RRF 融合。"""

import os, sqlite3, struct, json
import sqlite_vec
import structlog
from sentence_transformers import SentenceTransformer

DB_PATH = os.getenv("DATABASE_PATH", "data/knowledge.db")
BGE_PATH = os.getenv("BGE_MODEL_PATH", "data/bge-m3")
logger = structlog.get_logger()

_encoder = SentenceTransformer("BAAI/bge-m3", cache_folder=BGE_PATH)


def _encode(text: str) -> list[float]:
    return _encoder.encode(text, normalize_embeddings=True).tolist()


def _connect():
    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    return db


def keyword_search(query: str, limit: int = 10) -> list[dict]:
    db = _connect()
    fts_rows = db.execute(
        "SELECT rowid, rank FROM knowledge_fts WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, limit)
    ).fetchall()
    results = []
    for rowid, rank in fts_rows:
        e = db.execute("SELECT id, title, content FROM knowledge_entries WHERE rowid=?", (rowid,)).fetchone()
        if e:
            results.append({"id": e[0], "title": e[1], "content": e[2], "score": -rank})
    return results


def vec_search(query: str, limit: int = 10) -> list[dict]:
    db = _connect()
    qvec = _encode(query)
    rows = db.execute(
        """SELECT k.id, k.title, k.content, vec_distance_cosine(v.embedding, ?) AS dist
           FROM knowledge_vec v JOIN knowledge_entries k ON k.rowid = v.rowid
           ORDER BY dist LIMIT ?""",
        (struct.pack("1024f", *qvec), limit)
    ).fetchall()
    return [{"id": r[0], "title": r[1], "content": r[2], "score": 1.0 - r[3]} for r in rows]


def reciprocal_rank_fusion(a: list[dict], b: list[dict], k: int = 60, alpha: float = 0.5) -> list[dict]:
    scores = {}
    for rank, item in enumerate(a):
        scores[item["id"]] = scores.get(item["id"], 0) + alpha / (k + rank + 1)
    for rank, item in enumerate(b):
        scores[item["id"]] = scores.get(item["id"], 0) + (1 - alpha) / (k + rank + 1)
    merged = [{"id": _id, "score": s} for _id, s in scores.items()]
    merged.sort(key=lambda x: x["score"], reverse=True)
    for m in merged:
        for src in a + b:
            if src["id"] == m["id"]:
                m["title"] = src["title"]
                m["content"] = src["content"]
                break
    return merged


def hybrid_search(query: str, top_k: int = 5) -> list[dict]:
    kw = keyword_search(query, limit=top_k * 2)
    vec = vec_search(query, limit=top_k * 2)
    fused = reciprocal_rank_fusion(kw, vec)
    result = fused[:top_k]
    logger.info("hybrid_search", query=query, kw_count=len(kw), vec_count=len(vec), result_count=len(result))
    return result
