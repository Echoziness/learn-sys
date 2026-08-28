"""混合检索：FTS5 关键词 + sqlite-vec 向量 + RRF 融合，附检索覆盖度判定。

无模块级副作用：Retriever 由组合根实例化（传入 DB 路径与 encoder），方法均为同步实现，
调用方（graph 节点）负责用 asyncio.to_thread 包装以避免阻塞事件循环。
"""

from __future__ import annotations

import sqlite3
import struct
from contextlib import closing
from typing import Protocol

import sqlite_vec
import structlog

from core.state import RetrievedEntry

logger = structlog.get_logger()


class Encoder(Protocol):
    """sentence-transformers 的最小协议。测试可用 fake 注入，避免加载真实模型。"""

    def encode(self, text: str, normalize_embeddings: bool = True) -> list[float]: ...
    def get_sentence_embedding_dimension(self) -> int: ...


def segment_cjk(text: str) -> str:
    """在相邻 CJK 字符间插入空格。unicode61 会把连续中文视为单个长 token，
    逐字切分后中文子串检索才可用（索引与查询必须使用同一套分词）。"""
    out: list[str] = []
    for i, ch in enumerate(text):
        if i > 0 and "一" <= ch <= "鿿" and "一" <= text[i - 1] <= "鿿":
            out.append(" ")
        out.append(ch)
    return "".join(out)


def fts_document(title: str, content: str, keywords: list[str]) -> str:
    """入库侧的 FTS 文档构造：与查询侧同一套 CJK 分词。"""
    return segment_cjk(" ".join([title, content, *keywords]))


def sanitize_fts_query(query: str) -> str:
    """把 LLM 生成的自由文本转为安全的 FTS5 查询：CJK 逐字切分后逐词双引号包裹（内部引号转义）。
    未转义的引号/冒号/操作符会导致 MATCH 抛 OperationalError 并中断整个图运行。"""
    terms = [t.strip() for t in segment_cjk(query).split() if t.strip()]
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


class GapSearchResult:
    def __init__(self, entries: list[RetrievedEntry], uncovered_gaps: list[str]):
        self.entries = entries
        self.uncovered_gaps = uncovered_gaps


class Retriever:
    def __init__(
        self,
        db_path: str,
        encoder: Encoder,
        rrf_k: int = 60,
        coverage_min_score: float = 0.30,
    ):
        self._db_path = db_path
        self._encoder = encoder
        self._rrf_k = rrf_k
        self._coverage_min_score = coverage_min_score
        self._vec_dim = encoder.get_sentence_embedding_dimension()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path)
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        return db

    def keyword_search(
        self,
        query: str,
        limit: int = 10,
        max_difficulty: int | None = None,
        domain: str | None = None,
    ) -> list[RetrievedEntry]:
        fts_query = sanitize_fts_query(query)
        if not fts_query:
            return []
        with closing(self._connect()) as db:
            # 多域库防跨域污染：domain 限定检索只在同域条目内进行（None = 不过滤，单域库兼容）
            clauses, params = [], []
            if max_difficulty is not None:
                clauses.append("k.difficulty <= ?")
                params.append(max_difficulty)
            if domain is not None:
                clauses.append("k.domain = ?")
                params.append(domain)
            where = ("AND " + " AND ".join(clauses)) if clauses else ""
            params = [fts_query, *params, limit]
            rows = db.execute(
                f"""SELECT f.entry_id, f.rank, k.title, k.content
                   FROM knowledge_fts f JOIN knowledge_entries k ON k.id = f.entry_id
                   WHERE knowledge_fts MATCH ? {where} ORDER BY f.rank LIMIT ?""",
                params,
            ).fetchall()
        return [RetrievedEntry(id=r[0], title=r[2], content=r[3], score=-r[1]) for r in rows]

    def vec_search(
        self,
        query: str,
        limit: int = 10,
        max_difficulty: int | None = None,
        domain: str | None = None,
    ) -> list[RetrievedEntry]:
        qvec = self._encoder.encode(query, normalize_embeddings=True)
        with closing(self._connect()) as db:
            clauses, params = [], []
            if max_difficulty is not None:
                clauses.append("k.difficulty <= ?")
                params.append(max_difficulty)
            if domain is not None:
                clauses.append("k.domain = ?")
                params.append(domain)
            where = ("AND " + " AND ".join(clauses)) if clauses else ""
            rows = db.execute(
                f"""SELECT k.id, k.title, k.content, vec_distance_cosine(v.embedding, ?) AS dist
                   FROM knowledge_vec v JOIN knowledge_entries k ON k.rowid = v.rowid
                   WHERE 1=1 {where}
                   ORDER BY dist LIMIT ?""",
                (struct.pack(f"{self._vec_dim}f", *qvec), *params, limit),
            ).fetchall()
        return [RetrievedEntry(id=r[0], title=r[1], content=r[2], score=1.0 - r[3]) for r in rows]

    def reciprocal_rank_fusion(
        self, a: list[RetrievedEntry], b: list[RetrievedEntry], alpha: float = 0.5
    ) -> list[RetrievedEntry]:
        scores: dict[str, float] = {}
        by_id: dict[str, RetrievedEntry] = {}
        for rank, item in enumerate(a):
            scores[item.id] = scores.get(item.id, 0.0) + alpha / (self._rrf_k + rank + 1)
            by_id.setdefault(item.id, item)
        for rank, item in enumerate(b):
            scores[item.id] = scores.get(item.id, 0.0) + (1 - alpha) / (self._rrf_k + rank + 1)
            by_id.setdefault(item.id, item)
        fused = [
            RetrievedEntry(id=_id, title=by_id[_id].title, content=by_id[_id].content, score=s)
            for _id, s in scores.items()
        ]
        fused.sort(key=lambda x: x.score, reverse=True)
        return fused

    def hybrid_search(
        self,
        query: str,
        top_k: int = 5,
        max_difficulty: int | None = None,
        domain: str | None = None,
    ) -> list[RetrievedEntry]:
        kw = self.keyword_search(query, limit=top_k * 2, max_difficulty=max_difficulty, domain=domain)
        vec = self.vec_search(query, limit=top_k * 2, max_difficulty=max_difficulty, domain=domain)
        fused = self.reciprocal_rank_fusion(kw, vec)[:top_k]
        logger.info(
            "hybrid_search", query=query, kw_count=len(kw), vec_count=len(vec), result_count=len(fused)
        )
        return fused

    def search_gaps(
        self,
        gaps: list[str],
        top_k: int = 5,
        max_difficulty: int | None = None,
        domain: str | None = None,
    ) -> GapSearchResult:
        """按盲区逐条检索并判定覆盖度。max_difficulty 为可选的难度闸门；
        domain 限定同域检索（多域库防跨域污染，None = 不过滤）。"""
        seen: set[str] = set()
        entries: list[RetrievedEntry] = []
        uncovered: list[str] = []
        for gap in gaps:
            kw = self.keyword_search(gap, limit=top_k * 2, max_difficulty=max_difficulty, domain=domain)
            vec = self.vec_search(gap, limit=top_k * 2, max_difficulty=max_difficulty, domain=domain)
            best_vec = vec[0].score if vec else 0.0
            if not kw and best_vec < self._coverage_min_score:
                uncovered.append(gap)
                logger.info("gap_uncovered", gap=gap, best_vec_score=round(best_vec, 3))
                continue
            for e in self.reciprocal_rank_fusion(kw, vec)[:3]:
                if e.id not in seen:
                    seen.add(e.id)
                    entries.append(e)
        logger.info("retrieve_done", queries=len(gaps), entries=len(entries), uncovered=len(uncovered),
                    max_difficulty=max_difficulty)
        return GapSearchResult(entries=entries, uncovered_gaps=uncovered)
