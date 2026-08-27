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

# 难度层级 → 条目难度容忍带上限（画像-资源适配率指标的判定口径，PRD §5）。
# 带 = 严格层级上限 + 1：诊断层级是单次 LLM 推断（对照画像层级准确率实测仅 0.5），
# 判定带一个 ±1 级的不确定带才科学；且前置链闭包拉入的相邻难度前置条目是
# 正确的教学行为，不应记为失配。偏离容忍带 2 级及以上才降级标记 capped。
_DIFFICULTY_CAP = {"beginner": 3, "intermediate": 4, "advanced": 5}


def difficulty_tier_for(level: str, entry_difficulty: int) -> str:
    """资源难度层级：条目难度在层级容忍带（上限+1）内用层级本身，超出降级标记 capped。

    适配率指标（evals/metrics.py）以本函数输出为判定输入——
    资源包难度层级与诊断难度层级一致（未 capped）即记一次适配。
    """
    cap = _DIFFICULTY_CAP.get(level, 5)
    return level if entry_difficulty <= cap else f"capped:{level}"


def is_tier_matched(tier: str) -> bool:
    """资源难度层级是否与诊断层级匹配（非 capped——容忍带内即适配）。"""
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

# 学习者指涉标记（导出过滤 + 自检共用）：知识库条目必须与学习者无关，
# 含这些指涉的论断是面向当前学生的个性化教学（重教轮画像适配/错因纠正），
# 讲义保留（教学上正确），但不得进可复用知识条目。
# 不用裸「你」判——全库 2168 条讲义论断中 642 条含教学口吻的「你可以…」，
# 属正常知识表述，误伤面不可接受；只锁画像/会话特定指涉的强模式。
PERSONAL_MARKERS = (
    r"你的错误",
    r"该(?:学生|学员|学习者)",
    r"对于[^。；]{0,20}的你",
    r"对你而言",
    r"你熟悉的",  # 「以你熟悉的X为例」= 画像背景引入（p42 实测："以你熟悉的SQL单表查询为例"）
    r"您",  # 第二人称敬语属对话措辞；全库 2168 条讲义论断仅 14 条命中，误伤面可忽
)

# 换皮重复判定的二字组重叠阈值：待收论断的 bigram 与已收内容重叠达阈即视为
# 同一知识换说法。阈值演进：初设 0.5（拦截 0.54-0.83 的硬重复）；p13 实测
# 发现跨轮软重复集中在 0.33-0.43 区间（同规则换说法/同示例变体），收紧到 0.40——
# 三会话 9 处差异逐一抽查均为真重复无误杀；0.30 以下开始误伤真增量不用。
EXPORT_DEDUP_OVERLAP = 0.40


def _bigrams(text: str) -> set[str]:
    """二字组集合（去空白小写，与出题/误区校验同粒度）。"""
    t = re.sub(r"\s+", "", text.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def has_personal_reference(text: str) -> bool:
    """文本是否含学习者指涉（画像适配/会话特定的第二人称表达）。"""
    return any(re.search(p, text) for p in PERSONAL_MARKERS)


def _lecture_to_knowledge(lecture: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """讲义 → 可复用知识论断（导出专用三重过滤）。

    1. 剔除 extension（错因扩展层：面向该学生错因的定向讲解，不是知识）；
    2. 剔除学习者指涉论断（重教轮画像适配措辞如「对于化学专业的你而言」）；
    3. 跨轮换皮去重（bigram 重叠 ≥ 阈值即弃——taught_previously 只防复读不防换说法，
       实测 16 条论断同一知识讲 3 遍）。讲义本体不受影响：个性化教学是学生面的功能。
    """
    kept: list[dict[str, Any]] = []
    kept_bg: set[str] = set()
    for c in lecture:
        if c.get("claim_type") == "extension":
            continue
        text = c.get("text", "")
        if has_personal_reference(text):
            continue
        bg = _bigrams(text)
        if bg and len(bg & kept_bg) / len(bg) >= EXPORT_DEDUP_OVERLAP:
            continue
        kept.append(c)
        kept_bg |= bg
    return kept


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

    - content = 讲义论断经**导出三重过滤**（剔 extension / 剔学习者指涉 /
      跨轮换皮去重，见 _lecture_to_knowledge）后拼接 + 可选"常见误区"段
      （distill 提炼物，原料本身不进库）；讲义本体保留个性化论断（教学功能），
      只在入库形态收口——可复用知识必须与学习者无关；
    - keywords 过滤到 content 实际命中的（判分/种子校验同语义：字符子集），
      保证导出条目天然通过"关键词字符 ⊆ content"校验；
    - prerequisites / difficulty / knowledge_type 继承源条目（知识依赖不变）；
    - source 改写为生成溯源链：由哪条权威条目生成、讲义论断数（讲义只收
      supported 论断，故全部审核通过）与知识化入库条数。
    过滤后无知识可复用返回 None。claims_total 参数已弃用（事件口径分母在
    多轮重教会话失真，讲义只含 supported 也使通过率恒为 100%），保留签名兼容。
    """
    lecture = [c for c in (pkg.get("lecture") or []) if isinstance(c, dict) and c.get("text")]
    kept = _lecture_to_knowledge(lecture)
    if not kept:
        return None
    body = "\n\n".join(c["text"] for c in kept)
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
            f"讲义论断 {len(lecture)} 条（均经审核通过），知识化入库 {len(kept)} 条"
        ),
    }


__all__ = [
    "CHALLENGE_MASTERY_GATE",
    "EXPORT_DEDUP_OVERLAP",
    "PERSONAL_MARKERS",
    "archive_questions",
    "build_challenge",
    "build_lecture",
    "difficulty_tier_for",
    "extract_practice",
    "has_personal_reference",
    "is_tier_matched",
    "package_to_entry",
]
