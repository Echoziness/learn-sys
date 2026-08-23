"""资源包组装（W1）：审核通过论断 + 归档题目 + 指南提取 + 进阶标记。

纯函数、无 I/O——DB 写入由 teach_loop 调 SessionStore 完成。
三形态对应赛题主交付物（PRD §3.3）：
- 定制化讲义 lecture：通过审核的论断（带 evidence 溯源链）；
- 分阶测试题 questions：choice / scaffold / answer 三阶归档；
- 实操指南 practice：procedure 条目的步骤化指南（来自 generate 的 procedure 段）。

条目化导出（2026-08-23 拍板）：产出物可复用是赛题硬要求。复用形态 =
package_to_entry 把资源包提炼为与知识库 entries.jsonl 完全同构的条目
（导出物可被 init_db 原样入库——同规范的硬证明）。进库的是知识本身：
讲义论断已是审核过的知识文本；错题/脚手架只是 distill agent 的提炼
原料，其产物（误区知识）以"常见误区"段落进入 content。
"""

from __future__ import annotations

import re
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


# 诊断层级的中文标注（导出条目标题用）
_LEVEL_LABEL = {"beginner": "零基础", "intermediate": "进阶", "advanced": "高级"}


def package_to_entry(
    pkg: dict[str, Any],
    source_entry: Any,
    *,
    learner_id: str,
    difficulty_level: str,
    claims_total: int,
    pitfalls: list[str] | None = None,
) -> dict[str, Any] | None:
    """资源包 → 知识库条目（entries.jsonl 同构，SeedEntry 可直接校验入库）。

    - content = 讲义 supported 论断按教学弧顺序拼接（procedure_guide 步骤
      也经 build_lecture 收入 lecture，天然在内容里）+ 可选"常见误区"段
      （distill 提炼物，原料本身不进库）；
    - keywords 过滤到 content 实际命中的（判分/种子校验同语义：字符子集），
      保证导出条目天然通过"关键词字符 ⊆ content"校验；
    - prerequisites / difficulty / knowledge_type 继承源条目（知识依赖不变）；
    - source 改写为生成溯源链：由哪条权威条目生成、审核通过率。
    讲义为空返回 None（无知识可复用的包不导出）。
    """
    lecture = [c for c in (pkg.get("lecture") or []) if isinstance(c, dict) and c.get("text")]
    if not lecture:
        return None
    body = "\n\n".join(c["text"] for c in lecture)
    pit_list = [p for p in (pitfalls or []) if p]
    if pit_list:
        # 提炼物可能自带"常见误区："前缀（LLM 照抄模板）——去重后统一加一次
        stripped = [re.sub(r"^(常见误区[:：]\s*)+", "", p).rstrip("。") for p in pit_list]
        body += "\n\n常见误区：" + "；".join(stripped) + "。"

    content_chars = set(re.sub(r"\s+", "", body.lower()))
    keywords = [
        kw
        for kw in getattr(source_entry, "keywords", [])
        if set(re.sub(r"\s+", "", kw.lower())) <= content_chars
    ]
    label = _LEVEL_LABEL.get(difficulty_level, difficulty_level)
    return {
        "id": f"GEN-{source_entry.id}-{learner_id}",
        "knowledge_type": getattr(source_entry, "knowledge_type", "concept"),
        "title": f"{source_entry.title}（{label}适配版）",
        "content": body,
        "prerequisites": list(getattr(source_entry, "prerequisites", [])),
        "difficulty": getattr(source_entry, "difficulty", 1),
        "keywords": keywords,
        "source": (
            f"生成自 {source_entry.id}（{getattr(source_entry, 'source', '')}）；"
            f"审核通过 {len(lecture)}/{claims_total} 论断"
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
    "package_to_entry",
]
