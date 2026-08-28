"""种子数据全量校验：schema 约束、前置存在/无环/难度单调、关键词字符在 content 内。

与判分同口径：grade_answer 判定一个关键词命中 = 该关键词全部字符（去空格、小写）
都出现在作答中（AGENTS.md §6 的 CJK 坑）。content 长度只做宽松 sanity——
50-100 字的目标约束针对新增条目（写作规范，见 AGENTS.md §3.6），既有条目内容不在此列。

多域（2026-08-28）：每域独立校验 + 跨域 id 唯一（领域间无前置依赖——
前置闭包在 plan 内按域计算，跨域引用会被 plan 的 ID 投影天然丢弃）。
"""

import re
from pathlib import Path

import pytest
from scripts.init_db import KnowledgeType, load_entries

from core.plan import KnowledgeEntry, topo_sort

SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "seeds"


@pytest.fixture(scope="module")
def domains() -> dict:
    return load_entries(SEED_DIR)


@pytest.fixture(scope="module")
def entries(domains) -> list:
    return [e for es in domains.values() for e in es]


def _content_chars(text: str) -> set[str]:
    return set(re.sub(r"\s+", "", text.lower()))


def test_entries_count_and_id_format(entries):
    assert len(entries) == 49
    for e in entries:
        assert re.fullmatch(r"[A-Z]+-[A-Z]+-\d{3}", e.id), f"id 格式非法: {e.id}"


def test_domains_present(domains):
    """多域并存：每域有种子，id 前缀与域名对应（LNX/BDA），跨域不重号。"""
    assert set(domains) == {"bigdata-analysis", "linux-ops"}
    assert len(domains["bigdata-analysis"]) == 31
    assert len(domains["linux-ops"]) == 18
    for e in domains["linux-ops"]:
        assert e.id.startswith("LNX-")
    for e in domains["bigdata-analysis"]:
        assert e.id.startswith("BDA-")


def test_knowledge_type_valid_and_covered(entries):
    types = {e.knowledge_type for e in entries}
    assert types == {
        KnowledgeType.memory,
        KnowledgeType.concept,
        KnowledgeType.procedure,
    }


def test_keywords_chars_are_in_content(entries):
    """每个关键词的全部字符必须出现在 content 中——否则判分子串匹配必失配。"""
    for e in entries:
        chars = _content_chars(e.content)
        for kw in e.keywords:
            kw_chars = _content_chars(kw)
            assert kw_chars, f"{e.id} 空关键词"
            assert kw_chars.issubset(chars), f"{e.id} 关键词 {kw} 的字符未全部出现在 content"


def test_keywords_count(entries):
    for e in entries:
        assert 3 <= len(e.keywords) <= 7, f"{e.id} keywords 数量 {len(e.keywords)}"


def test_prerequisites_exist_and_difficulty_monotonic(entries):
    by_id = {e.id: e for e in entries}
    for e in entries:
        for pre in e.prerequisites:
            assert pre in by_id, f"{e.id} 前置 {pre} 不存在"
            assert e.difficulty >= by_id[pre].difficulty, (
                f"{e.id} 难度 {e.difficulty} 小于前置 {pre} 难度 {by_id[pre].difficulty}"
            )


def test_no_prereq_cycles(entries):
    by_id = {e.id: e for e in entries}
    gray, black = 1, 2
    color: dict[str, int] = {eid: 0 for eid in by_id}

    def visit(eid: str, trail: list[str]) -> None:
        color[eid] = gray
        for pre in by_id[eid].prerequisites:
            assert color[pre] != gray, f"成环: {' -> '.join(trail + [pre, eid])}"
            if color[pre] == 0:
                visit(pre, trail + [eid])
        color[eid] = black

    for eid in by_id:
        if color[eid] == 0:
            visit(eid, [])


def test_topo_sort_covers_all_entries(entries):
    """拓扑排序不丢节点 = 前置关系完整无环（plan 的降维基础）。"""
    by_id = {
        e.id: KnowledgeEntry(
            id=e.id,
            title=e.title,
            content=e.content,
            prerequisites=e.prerequisites,
            difficulty=e.difficulty,
            keywords=e.keywords,
            source=e.source,
        )
        for e in entries
    }
    ordered = topo_sort(by_id, set(by_id))
    assert len(ordered) == 49


def test_content_length_sanity(entries):
    for e in entries:
        assert 20 <= len(e.content) <= 300, f"{e.id} content 长度 {len(e.content)} 越界"
