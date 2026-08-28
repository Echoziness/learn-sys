"""retrieve_node 锚定逻辑：当前主题条目强制进上下文，检索结果去重补充。"""

import asyncio

from core.graph import retrieve_node
from core.plan import KnowledgeEntry
from core.retrieval import GapSearchResult
from core.state import AgentState, RetrievedEntry


class FakeRetriever:
    """返回固定检索结果，供 retrieve_node 锚定逻辑测试。"""

    def __init__(self, entries, uncovered=None):
        self._entries = entries
        self._uncovered = uncovered or []

    def search_gaps(self, gaps, top_k=5, max_difficulty=None, domain=None):
        return GapSearchResult(self._entries, self._uncovered)


def test_anchor_entry_always_first():
    anchor = KnowledgeEntry(id="T1", title="数据预处理概述", content="锚定内容")
    others = [
        RetrievedEntry(id="T2", title="可视化原则", content="别的", score=0.5),
        RetrievedEntry(id="T1", title="数据预处理概述", content="锚定内容", score=0.4),
    ]
    state: AgentState = {
        "gaps": ["数据预处理概述"],
        "anchor_entry": anchor,
        "difficulty_level": "beginner",
    }
    result = asyncio.run(retrieve_node(state, retriever=FakeRetriever(others), top_k=5))
    entries = result["retrieved_entries"]
    assert entries[0].id == "T1"  # 锚定条目强制在首位
    assert entries[0].score == 1.0
    assert len(entries) == 2  # 检索结果中重复的 T1 被去重


def test_anchor_entry_deduplicates_retrieval():
    anchor = KnowledgeEntry(id="T1", title="数据预处理概述", content="锚定内容")
    others = [
        RetrievedEntry(id="T1", title="数据预处理概述", content="锚定内容", score=0.4),
        RetrievedEntry(id="T3", title="清洗", content="别的", score=0.3),
    ]
    state: AgentState = {
        "gaps": ["数据预处理概述"],
        "anchor_entry": anchor,
        "difficulty_level": "beginner",
    }
    result = asyncio.run(retrieve_node(state, retriever=FakeRetriever(others), top_k=5))
    ids = [e.id for e in result["retrieved_entries"]]
    assert ids == ["T1", "T3"]


def test_anchor_entry_missing_falls_back_to_retrieval():
    """无锚定（旧调用方）时保持原有检索行为。"""
    others = [
        RetrievedEntry(id="T2", title="可视化", content="别的", score=0.5),
        RetrievedEntry(id="T3", title="清洗", content="别的", score=0.3),
    ]
    state: AgentState = {"gaps": ["数据预处理概述"], "difficulty_level": "beginner"}
    result = asyncio.run(retrieve_node(state, retriever=FakeRetriever(others), top_k=5))
    assert [e.id for e in result["retrieved_entries"]] == ["T2", "T3"]


def test_retrieve_node_passes_domain_to_retriever():
    """多域库：state 携带的 domain 透传给检索器（同域检索防跨域污染）。"""
    captured = {}

    class DomainCaptureRetriever:
        def search_gaps(self, gaps, top_k=5, max_difficulty=None, domain=None):
            captured["domain"] = domain
            return GapSearchResult([], [])

    state: AgentState = {
        "gaps": ["任意"],
        "difficulty_level": "beginner",
        "domain": "ml-basics",
    }
    asyncio.run(retrieve_node(state, retriever=DomainCaptureRetriever(), top_k=5))
    assert captured["domain"] == "ml-basics"

    # 单域部署（state 无 domain key）：不过滤，行为不变
    asyncio.run(
        retrieve_node({"gaps": ["任意"], "difficulty_level": "beginner"},
                      retriever=DomainCaptureRetriever(), top_k=5)
    )
    assert captured["domain"] is None
