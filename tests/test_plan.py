"""课程切片：gap 匹配、前置链补全、难度过滤、拓扑排序。"""

from core.plan import KnowledgeEntry, build_plan, match_gap_to_entry, topo_sort


def _entry(eid, title, prerequisites=(), difficulty=1, keywords=()):
    return KnowledgeEntry(
        id=eid,
        title=title,
        content="",
        prerequisites=list(prerequisites),
        difficulty=difficulty,
        keywords=list(keywords),
    )


def _entries():
    return [
        _entry("DB", "关系型数据库基本概念", keywords=["数据库", "关系型", "表", "主键"]),
        _entry("SEL", "SELECT 基础查询", ["DB"], difficulty=2, keywords=["SELECT", "查询", "FROM"]),
        _entry("AGG", "聚合查询", ["SEL"], difficulty=3, keywords=["聚合", "GROUP BY", "统计"]),
        _entry("JOIN", "多表连接", ["SEL"], difficulty=3, keywords=["JOIN", "连接", "多表"]),
    ]


def test_match_gap_hits_keyword():
    entries = _entries()
    matched = match_gap_to_entry("我想学聚合查询", entries)
    assert matched is not None
    assert matched.id == "AGG"
    matched = match_gap_to_entry("数据库基本概念是什么", entries)
    assert matched is not None
    assert matched.id == "DB"


def test_match_gap_miss_returns_none():
    entries = _entries()
    assert match_gap_to_entry("机器学习回归模型", entries) is None


def test_build_plan_topological_with_prereq_closure():
    """盲区只有聚合查询时，前置链（SELECT→DB）必须被补入切片。"""
    plan = build_plan(_entries(), ["聚合查询"])
    ids = [t.entry_id for t in plan.topics]
    assert ids == ["DB", "SEL", "AGG"]  # 拓扑序：前置先教
    target = {t.entry_id: t for t in plan.topics}
    assert target["AGG"].target is True
    assert target["DB"].target is False  # 前置链补入，非盲区直接命中


def test_build_plan_difficulty_gate_cuts_chain():
    """难度闸门：高阶目标被滤掉时显式记入 uncovered，不静默。"""
    plan = build_plan(_entries(), ["聚合查询"], max_difficulty=2)
    assert "聚合查询" in plan.uncovered_gaps
    ids = [t.entry_id for t in plan.topics]
    assert "AGG" not in ids  # 难度 3 被滤掉
    assert "SEL" in ids  # 前置链仍在


def test_build_plan_multiple_gaps_dedup():
    plan = build_plan(_entries(), ["聚合查询", "多表连接"])
    ids = [t.entry_id for t in plan.topics]
    assert ids[0:2] == ["DB", "SEL"]  # 前置链固定在前
    assert set(ids[2:]) == {"AGG", "JOIN"}  # 同层顺序无意义，集合断言
    assert len(ids) == len(set(ids))  # 前置闭包不产生重复


def test_build_plan_uncovered_reported():
    plan = build_plan(_entries(), ["聚合查询", "推荐系统算法"])
    assert plan.uncovered_gaps == ["推荐系统算法"]


def test_topo_sort_cycle_survives():
    """环是数据错误：不崩溃、环节点被安全跳过（数据质量应在前置校验）。"""
    entries = {
        "A": _entry("A", "A", ["B"]),
        "B": _entry("B", "B", ["A"]),
    }
    ids = {"A", "B"}
    ordered = topo_sort(entries, ids)
    assert ordered == []
