"""课程切片——把诊断盲区投影到知识本体，补全前置链，拓扑排序。

全链路设计（2026-07-22 拍板）：课程本体静态（数据阶段定），课程切片由确定性
算法推导，交流结果只约束教学执行层。本模块是切片的唯一实现——纯函数，无
I/O、无 LLM：

1. gap 文本 → 本体条目（标题/关键词匹配，匹配不上进 uncovered）；
2. 补全前置链：目标的前置即使不在盲区也要加入切片——这是"降维"的静态版，
   "回前置"只是回到切片中前一个未达标主题，运行时无需动态改课程；
3. 难度过滤（difficulty 闸门）；
4. prerequisites 拓扑排序（前置先教）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class KnowledgeEntry:
    """知识本体条目——与 scripts/init_db.SeedEntry 同构，core 层自持副本，
    避免依赖脚本层。切片/匹配/出题统一消费此模型。"""

    id: str
    title: str
    content: str
    prerequisites: list[str] = field(default_factory=list)
    difficulty: int = 1
    keywords: list[str] = field(default_factory=list)
    source: str = ""
    # memory（事实/定义/术语）/ concept（概念与关系）/ procedure（步骤技能）。
    # 决定 assess 题型分发；缺失时按 concept 处理（与 DB 默认一致）。
    knowledge_type: str = "concept"


@dataclass
class PlanTopic:
    """切片中的一个主题：条目 + 教学顺序。"""

    entry_id: str
    title: str
    order: int
    # 该主题是否由"盲区直接命中"（False = 因前置链补入，只教不考通关）。
    target: bool


@dataclass
class Plan:
    topics: list[PlanTopic] = field(default_factory=list)
    uncovered_gaps: list[str] = field(default_factory=list)


@lru_cache(maxsize=256)
def _tokenize(text: str) -> tuple[str, ...]:
    """CJK 逐字切分 + 拉丁词小写。与检索层 segment_cjk 同语义：相邻 CJK
    字符间必须切分，否则中文短语会变成一个无法命中子串的整词。"""
    out: list[str] = []
    for ch in text.lower():
        if "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
            out.append(" ")  # 逐字切分
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append(" ")
    return tuple(t for t in "".join(out).split() if t)


def match_gap_to_entry(
    gap: str, entries: list[KnowledgeEntry], min_overlap: float = 0.4
) -> KnowledgeEntry | None:
    """把自由文本 gap 匹配到本体条目。

    匹配分 = 命中词数 / 条目关键词长度（gap 词必须包含条目的关键词才算命中）。
    阈值 0.4：命中一半关键词即视为匹配，宁少勿错——匹配不上走 uncovered，
    绝不模糊映射到错误条目。
    """
    gap_tokens = set(_tokenize(gap))
    if not gap_tokens:
        return None
    best: KnowledgeEntry | None = None
    best_score = 0.0
    for entry in entries:
        flat: set[str] = set(_tokenize(entry.title))
        for kw in entry.keywords:
            flat.update(_tokenize(kw))
        if not flat:
            continue
        overlap = len(gap_tokens & flat) / len(flat)
        if overlap > best_score:
            best_score = overlap
            best = entry
    return best if best_score >= min_overlap else None


def _collect_with_prereqs(entries: dict[str, KnowledgeEntry], wanted: set[str]) -> set[str]:
    """闭包：目标及其前置、前置的前置…… 全部纳入切片。"""
    result: set[str] = set()
    stack = list(wanted)
    while stack:
        eid = stack.pop()
        if eid in result or eid not in entries:
            continue
        result.add(eid)
        stack.extend(entries[eid].prerequisites)
    return result


def topo_sort(entries: dict[str, KnowledgeEntry], ids: set[str]) -> list[str]:
    """Kahn 拓扑排序（prerequisites 先教）。环或缺失前置时跳过该节点。"""
    indegree = {eid: 0 for eid in ids}
    dependents: dict[str, list[str]] = {eid: [] for eid in ids}
    for eid in ids:
        for pre in entries[eid].prerequisites:
            if pre in ids:
                indegree[eid] += 1
                dependents[pre].append(eid)
    ready = sorted(eid for eid, d in indegree.items() if d == 0)
    ordered: list[str] = []
    while ready:
        eid = ready.pop(0)
        ordered.append(eid)
        for dep in dependents[eid]:
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
    return ordered


def build_plan(
    entries: list[KnowledgeEntry],
    gaps: list[str],
    *,
    max_difficulty: int = 5,
) -> Plan:
    """完整切片管线：匹配 → 闭包 → 难度过滤 → 拓扑排序。

    难度过滤发生在闭包之后：前置链中的高难条目会被滤掉，其依赖关系随之
    断裂——该分支视为不可教，由 uncover 机制暴露给上层（不静默吞掉）。
    """
    by_id = {e.id: e for e in entries}
    wanted: set[str] = set()
    uncovered: list[str] = []
    for gap in gaps:
        # gap 可能是本体 ID（diagnose 收敛输出）或自由文本（fallback 匹配）。
        matched = by_id.get(gap) or match_gap_to_entry(gap, entries)
        if matched is None:
            uncovered.append(gap)
            continue
        wanted.add(matched.id)
        # 该 gap 匹配到的条目如果被难度闸门过滤，也记入 uncovered（显式降级）。
        if matched.difficulty > max_difficulty:
            uncovered.append(gap)

    target_ids = set(wanted)
    with_prereqs = _collect_with_prereqs(by_id, wanted)
    within_difficulty = {
        eid for eid in with_prereqs if by_id[eid].difficulty <= max_difficulty
    }
    ordered = topo_sort(by_id, within_difficulty)

    plan = Plan(uncovered_gaps=uncovered)
    for order, eid in enumerate(ordered):
        entry = by_id[eid]
        plan.topics.append(
            PlanTopic(
                entry_id=eid,
                title=entry.title,
                order=order,
                target=eid in target_ids,
            )
        )
    return plan


__all__ = ["KnowledgeEntry", "PlanTopic", "Plan", "match_gap_to_entry", "build_plan", "topo_sort"]
