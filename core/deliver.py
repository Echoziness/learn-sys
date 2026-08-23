"""资源包组装（W1）：审核通过论断 + 归档题目 + 指南提取 + 进阶标记。

纯函数、无 I/O——DB 写入由 teach_loop 调 SessionStore 完成。
三形态对应赛题主交付物（PRD §3.3）：
- 定制化讲义 lecture：通过审核的论断（带 evidence 溯源链）；
- 分阶测试题 questions：choice / scaffold / answer 三阶归档；
- 实操指南 practice：procedure 条目的步骤化指南（来自 generate 的 procedure 段）。
"""

from __future__ import annotations

from typing import Any

from core.state import DraftClaim, ReviewNote

# 进阶挑战的掌握度门槛（PRD FR-10：双向决策的"进阶"侧）
CHALLENGE_MASTERY_GATE = 0.85

# 难度层级 → 允许的条目难度上限（画像-资源适配率指标的判定口径，PRD §5）
_DIFFICULTY_CAP = {"beginner": 2, "intermediate": 3, "advanced": 5}


def difficulty_tier_for(level: str, entry_difficulty: int) -> str:
    """资源难度层级：诊断层级允许该条目时用层级本身，超出时降级标记为 capped。

    适配率指标（evals/metrics.py）以本函数输出为判定输入——
    资源包难度层级与诊断难度层级一致（未 capped）即记一次适配。
    """
    cap = _DIFFICULTY_CAP.get(level, 5)
    return level if entry_difficulty <= cap else f"capped:{level}"


def is_tier_matched(tier: str) -> bool:
    """资源难度层级是否与诊断层级匹配（非 capped）。"""
    return not tier.startswith("capped:")


def build_lecture(
    claims: list[DraftClaim],
    reviews: list[ReviewNote],
    *,
    round_no: int = 1,
    round_by_index: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """讲义 = 审核通过（supported）的论断，保留溯源链与分层标记。

    输入应为该条目**全部轮次**的论断累积（taught_previously 保证各轮互补，
    只取最后一轮会丢内容——实测踩坑）；round_by_index 记录各论断的来源轮次。
    """
    verdicts: dict[int, str] = {}
    rank = {"unsupported": 0, "partially_supported": 1, "supported": 2}
    for note in reviews:
        cur = verdicts.get(note.claim_index)
        if cur is None or rank[note.verdict] < rank[cur]:
            verdicts[note.claim_index] = note.verdict
    lecture: list[dict[str, Any]] = []
    for claim in claims:
        if verdicts.get(claim.claim_index, "unsupported") != "supported":
            continue  # 未获支持的论断不进讲义（幻觉防控的资源侧收口）
        lecture.append(
            {
                "text": claim.text,
                "evidence_ids": claim.evidence_ids,
                "claim_type": claim.claim_type,
                "round": (round_by_index or {}).get(claim.claim_index, round_no),
            }
        )
    return lecture


def archive_questions(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分阶题归档：从已作答教学轮映射为学生可见形态（无 expected）。

    分阶语义 = 题型阶梯（choice 识别式 / scaffold 脚手架 / answer 回忆式），
    由掌握度驱动产生——归档顺序即学习者的实际攀升路径。
    """
    archived: list[dict[str, Any]] = []
    for r in rounds:
        q = r.get("question")
        if not q or r.get("answer") is None:
            continue
        archived.append(
            {
                "question_id": q["question_id"],
                "entry_id": q.get("entry_id", r["entry_id"]),
                "question_type": q["question_type"],
                "prompt": q["prompt"],
                "options": q.get("options", []),
                "round": r["round_no"],
            }
        )
    return archived


def _first_sentence(text: str, max_len: int = 32) -> str:
    """提取首句作为检查点描述（无句读符时按长度截断，不切断在标点后）。"""
    for sep in ("。", "；", ".", ";"):
        idx = text.find(sep)
        if 0 < idx + 1 <= max_len:
            return text[: idx + 1]
    return text[:max_len].rstrip()


def extract_practice(
    claims: list[DraftClaim],
    reviews: list[ReviewNote],
    *,
    knowledge_type: str,
) -> dict[str, Any] | None:
    """实操指南：procedure 条目上由 generate 产出的步骤化段落（claim_type=procedure_guide）。

    指南论断同样过审核——只有 supported 的步骤进入指南（幻觉率口径覆盖实操指南）。
    非 procedure 条目返回 None（资源包 practice 字段留空）。
    """
    if knowledge_type != "procedure":
        return None
    verdicts = {n.claim_index: n.verdict for n in reviews}
    steps = [
        {"text": c.text, "evidence_ids": c.evidence_ids}
        for c in claims
        if c.claim_type == "procedure_guide"
        and verdicts.get(c.claim_index, "unsupported") == "supported"
    ]
    if not steps:
        return None
    return {
        "steps": steps,
        "checkpoints": [_first_sentence(s["text"]) for s in steps],
    }


def build_challenge(
    topic_title: str,
    *,
    mastery: float,
    gate: float = CHALLENGE_MASTERY_GATE,
) -> dict[str, Any] | None:
    """进阶挑战任务（PRD FR-10）：高掌握度学习者的延伸任务标记，资源层实现。"""
    if mastery < gate:
        return None
    return {
        "type": "challenge",
        "topic": topic_title,
        "task": (
            f"尝试在不看讲义的情况下，向他人讲解「{topic_title}」的核心要点，"
            "并举一个讲义中没有的应用示例。"
        ),
    }


__all__ = [
    "CHALLENGE_MASTERY_GATE",
    "archive_questions",
    "build_challenge",
    "build_lecture",
    "difficulty_tier_for",
    "extract_practice",
    "is_tier_matched",
]
