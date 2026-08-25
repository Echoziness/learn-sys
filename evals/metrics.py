"""评测指标：赛题三项硬指标的代码级单一事实源（SSOT）。

口径与 PRD §5 一致：
- 幻觉率 = 最终裁决为 unsupported 的论断数 / 总论断数（fail-closed：无裁决视为 unsupported）
- 画像-资源适配率 = 资源包难度层级与诊断难度层级的匹配率（tier 非 capped）；
  判定容忍带 = 层级上限+1（诊断层级为单次 LLM 推断，带 ±1 级不确定带，
  口径事实源见 core/deliver.difficulty_tier_for）
- 知识点覆盖率 = 目标条目 keywords 在该条目资源包讲义文本中的覆盖比例（逐条目聚合）

每条论断恰好计入一次，不存在规则层/NLI 层双重计数（由 review.merge_verdicts 保证）。
CLI、pytest、批量评测（evals/run.py）必须共用此处实现，禁止就地另算。
"""

from __future__ import annotations

import re
from typing import Any

from core.deliver import is_tier_matched
from core.state import DraftClaim, ReviewNote


def claim_verdicts(draft: list[DraftClaim], reviews: list[ReviewNote]) -> dict[int, str]:
    """每条论断的最终裁决。同一条论断出现多条裁决时，unsupported 优先（fail-closed）。"""
    rank = {"unsupported": 0, "partially_supported": 1, "supported": 2}
    verdicts: dict[int, str] = {}
    for note in reviews:
        current = verdicts.get(note.claim_index)
        if current is None or rank[note.verdict] < rank[current]:
            verdicts[note.claim_index] = note.verdict
    for claim in draft:
        verdicts.setdefault(claim.claim_index, "unsupported")
    return verdicts


def hallucination_rate(draft: list[DraftClaim], reviews: list[ReviewNote]) -> float:
    """无溯源支持论断占比。空生成稿返回 0.0（无论断即无幻觉，由覆盖率指标另行约束）。"""
    if not draft:
        return 0.0
    verdicts = claim_verdicts(draft, reviews)
    unsupported = sum(1 for c in draft if verdicts[c.claim_index] == "unsupported")
    return unsupported / len(draft)


def tier_match_rate(packages: list[dict[str, Any]]) -> tuple[float, int, int]:
    """画像-资源适配率：资源包难度层级与诊断层级匹配（非 capped）的占比。

    返回 (rate, matched, total)。无资源包返回 (0.0, 0, 0)——
    由调用方决定无样本时如何呈现（评测脚本记为无效组）。
    """
    if not packages:
        return 0.0, 0, 0
    matched = sum(1 for p in packages if is_tier_matched(str(p.get("difficulty_tier", ""))))
    return matched / len(packages), matched, len(packages)


def _chars_in(keyword: str, text: str) -> bool:
    """keyword 去空格后全部字符出现在 text 中（与种子校验/判分同语义）。"""
    kw = re.sub(r"\s+", "", keyword.lower())
    if not kw:
        return False
    return set(kw).issubset(set(re.sub(r"\s+", "", text.lower())))


def keyword_coverage(
    packages: list[dict[str, Any]], keywords_by_entry: dict[str, list[str]]
) -> tuple[float, int, int]:
    """知识点覆盖率：目标条目 keywords 在该条目讲义文本中的覆盖比例（逐条目聚合）。

    讲义文本 = 资源包 lecture 各论断拼接（仅 supported 论断进讲义——由
    deliver.build_lecture 保证）。返回 (rate, hit, total)；无有效条目返回 0。
    """
    lecture_text = {
        p["entry_id"]: " ".join(
            c.get("text", "") if isinstance(c, dict) else str(c) for c in p.get("lecture", [])
        )
        for p in packages
        if p.get("entry_id")
    }
    hit = 0
    total = 0
    for entry_id, keywords in keywords_by_entry.items():
        if entry_id not in lecture_text:
            # 无资源包（未学到该条目）不计入分母——覆盖率衡量"已产资源的质量"
            continue
        text = lecture_text[entry_id]
        for kw in keywords:
            total += 1
            if _chars_in(kw, text):
                hit += 1
    return (hit / total if total else 0.0), hit, total


__all__ = [
    "claim_verdicts",
    "hallucination_rate",
    "tier_match_rate",
    "keyword_coverage",
]
