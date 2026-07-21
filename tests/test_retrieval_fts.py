"""FTS5 检索：查询转义防崩溃（重构前已验证 bug）+ CJK 逐字切分的子串召回。"""

import sqlite3

import pytest

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
    db.execute("CREATE TABLE knowledge_entries (id TEXT PRIMARY KEY, title TEXT, content TEXT)")
    db.execute("CREATE VIRTUAL TABLE knowledge_fts USING fts5(entry_id UNINDEXED, text)")
    db.execute(
        "INSERT INTO knowledge_entries VALUES ('E1', 'SQL 查询基础', 'SELECT 语句用于检索数据')"
    )
    db.execute(
        "INSERT INTO knowledge_fts VALUES ('E1', ?)",
        (fts_document("SQL 查询基础", "SELECT 语句用于检索数据", ["SQL", "查询"]),),
    )
    db.execute(
        "INSERT INTO knowledge_entries VALUES ('E2', '描述性统计', '均值与中位数')"
    )
    db.execute(
        "INSERT INTO knowledge_fts VALUES ('E2', ?)",
        (fts_document("描述性统计", "均值与中位数", ["统计"]),),
    )
    db.commit()
    db.close()
    return Retriever(db_path=db_path, encoder=FakeEncoder())


# 重构前实测会导致 OperationalError 的三类输入
DANGEROUS_QUERIES = ['SQL "查询', "统计: 均值", "SQL OR", 'JOIN (连接', 'NULL* AND']


@pytest.mark.parametrize("query", DANGEROUS_QUERIES)
def test_sanitize_prevents_crash(retriever, query):
    result = retriever.keyword_search(query)  # 不抛异常即通过
    assert isinstance(result, list)


def test_cjk_substring_matches(retriever):
    """unicode61 下"统计"本是独立长 token 无法命中"描述性统计"，逐字切分后必须命中。"""
    assert [e.id for e in retriever.keyword_search("统计")] == ["E2"]
    assert [e.id for e in retriever.keyword_search("SQL 查询")] == ["E1"]


def test_segment_cjk_splits_only_adjacent_cjk():
    assert segment_cjk("查询基础") == "查 询 基 础"
    assert segment_cjk("SQL 查询") == "SQL 查 询"
    assert segment_cjk("pandas数据处理") == "pandas数 据 处 理"


def test_empty_query_returns_empty(retriever):
    assert retriever.keyword_search("   ") == []
    assert sanitize_fts_query("") == ""
