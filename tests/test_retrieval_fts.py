"""FTS5 检索：查询转义防崩溃 + CJK 逐字切分 + 难度闸门筛选。"""

import sqlite3
import struct

import pytest
import sqlite_vec

from core.retrieval import Retriever, fts_document, sanitize_fts_query, segment_cjk


class FakeEncoder:
    def encode(self, text: str, normalize_embeddings: bool = True) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def get_sentence_embedding_dimension(self) -> int:
        return 4


@pytest.fixture
def retriever(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)

    db.execute(
        "CREATE TABLE knowledge_entries (id TEXT PRIMARY KEY, title TEXT, content TEXT, difficulty INTEGER)"
    )
    db.execute("CREATE VIRTUAL TABLE knowledge_fts USING fts5(entry_id UNINDEXED, text)")
    db.execute("CREATE VIRTUAL TABLE knowledge_vec USING vec0(embedding FLOAT[4])")

    # E1: difficulty 2 — SQL 查询基础
    db.execute("INSERT INTO knowledge_entries VALUES ('E1', 'SQL 查询基础', 'SELECT 语句用于检索数据', 2)")
    db.execute(
        "INSERT INTO knowledge_fts VALUES ('E1', ?)",
        (fts_document("SQL 查询基础", "SELECT 语句用于检索数据", ["SQL", "查询"]),),
    )
    db.execute(
        "INSERT INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
        (1, struct.pack("4f", 0.1, 0.2, 0.3, 0.4)),
    )

    # E2: difficulty 2 — 描述性统计
    db.execute("INSERT INTO knowledge_entries VALUES ('E2', '描述性统计', '均值与中位数', 2)")
    db.execute(
        "INSERT INTO knowledge_fts VALUES ('E2', ?)",
        (fts_document("描述性统计", "均值与中位数", ["统计"]),),
    )
    db.execute(
        "INSERT INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
        (2, struct.pack("4f", 0.5, 0.6, 0.7, 0.8)),
    )

    # E3: difficulty 4 — SQL JOIN（初学者不应看到）
    db.execute("INSERT INTO knowledge_entries VALUES ('E3', 'SQL 高级连接', 'INNER JOIN, LEFT JOIN', 4)")
    db.execute(
        "INSERT INTO knowledge_fts VALUES ('E3', ?)",
        (fts_document("SQL 高级连接", "INNER JOIN, LEFT JOIN", ["SQL", "JOIN"]),),
    )
    db.execute(
        "INSERT INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
        (3, struct.pack("4f", 0.2, 0.3, 0.1, 0.4)),
    )

    db.commit()
    db.close()
    return Retriever(db_path=db_path, encoder=FakeEncoder())


# 重构前实测会导致 OperationalError 的三类输入
DANGEROUS_QUERIES = ['SQL "查询', "统计: 均值", "SQL OR", 'JOIN (连接', 'NULL* AND']


@pytest.mark.parametrize("query", DANGEROUS_QUERIES)
def test_sanitize_prevents_crash(retriever, query):
    result = retriever.keyword_search(query)
    assert isinstance(result, list)


def test_cjk_substring_matches(retriever):
    assert [e.id for e in retriever.keyword_search("统计")] == ["E2"]
    assert [e.id for e in retriever.keyword_search("SQL 查询")] == ["E1"]


def test_segment_cjk_splits_only_adjacent_cjk():
    assert segment_cjk("查询基础") == "查 询 基 础"
    assert segment_cjk("SQL 查询") == "SQL 查 询"
    assert segment_cjk("pandas数据处理") == "pandas数 据 处 理"


def test_empty_query_returns_empty(retriever):
    assert retriever.keyword_search("   ") == []
    assert sanitize_fts_query("") == ""


def test_difficulty_filter_excludes_high_level_keyword(retriever):
    results = retriever.keyword_search("SQL", max_difficulty=2)
    ids = [e.id for e in results]
    assert "E1" in ids
    assert "E3" not in ids


def test_difficulty_filter_without_cap_returns_all(retriever):
    results = retriever.keyword_search("SQL")
    ids = [e.id for e in results]
    assert "E1" in ids
    assert "E3" in ids


def test_difficulty_filter_vec_search(retriever):
    results = retriever.vec_search("SQL 查询", max_difficulty=2)
    ids = [e.id for e in results]
    assert "E1" in ids
    assert "E3" not in ids


def test_search_gaps_respects_difficulty_cap(retriever):
    result = retriever.search_gaps(["SQL"], top_k=5, max_difficulty=2)
    ids = {e.id for e in result.entries}
    assert "E1" in ids
    assert "E3" not in ids  # difficulty 4 > cap 2 → 被过滤掉
