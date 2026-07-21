"""评测指标：赛题三项硬指标的代码级单一事实源（SSOT）。

口径与技术选型文档 §5 一致：
- 幻觉率 = 最终裁决为 unsupported 的论断数 / 总论断数（fail-closed：无裁决视为 unsupported）
- 每条论断恰好计入一次，不存在规则层/NLI 层双重计数（由 review.merge_verdicts 保证）
CLI、pytest、批量评测（evals/run.py）必须共用此处实现，禁止就地另算。
"""

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
