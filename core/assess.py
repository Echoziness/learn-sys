"""assess 与 feedback 的确定性核心——出题结构、判分、错因提取。

评分模式（2026-07-22 拍板）：确定性为主 + LLM 辅助——规则判对错（可复现、
可追溯），LLM 只负责把错因翻译成教学语言（在 feedback 节点接线，本模块不给
LLM 留任何裁决权）。

题型（2026-08-11 简化）：简答题 + 选择题两种，通用题型，与知识类型解耦——
knowledge_type 描述知识本体（影响教学方式/门槛/复习节奏），题型是评估手段。
出题由掌握度驱动（对齐 PRD 掌握度阶梯）：
- 掌握度低（< CHOICE_MASTERY_THRESHOLD）→ 选择题（识别式，脚手架）；
- 掌握度高 → 简答题（回忆/组织语言，苏格拉底式）。

TODO(operation)：真实操作题（SQLite 沙箱执行 SQL/pandas）因难度搁置，
未来在 procedure 条目上按此思路扩展。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from core.plan import KnowledgeEntry

# 选择题固定 4 选项；干扰项不足时允许少于 4（fail-closed：判分只认标签）。
NUM_CHOICE_OPTIONS = 4

# 掌握度低于此值出选择题（脚手架），达到后出简答题。对齐 PRD 阶梯：0.2-0.5 选择、0.5-0.7 问答。
CHOICE_MASTERY_THRESHOLD = 0.5


@dataclass(frozen=True)
class Question:
    """一道确定性考题：题干由条目生成，expected 是判分依据（服务端持有）。

    对齐 DeepTutor PendingQuestion 模式：expected 永不进学生视野，判分
    fail-closed——没有 expected 就判错，绝不判对。
    """

    question_id: str
    entry_id: str
    prompt: str
    question_type: str  # "choice" | "answer"
    expected_keywords: tuple[str, ...]
    options: tuple[str, ...] = ()  # choice 题：完整选项文本（带标签，如 "A. ..."）
    expected_label: str = ""  # choice 题：正确选项标签（"A"/"B"/...）


@dataclass(frozen=True)
class GradeResult:
    is_correct: bool
    matched: tuple[str, ...]  # 学生作答中命中的关键词（choice 题为空）
    missing: tuple[str, ...]  # 应覆盖但未命中的关键词（choice 题为空）
    keyword_coverage: float
    correct_label: str = ""  # choice 题：正确选项标签，供反馈使用


def build_question(
    entry: KnowledgeEntry,
    *,
    distractors: list[KnowledgeEntry] | None = None,
    mastery: float = 0.0,
) -> Question:
    """按掌握度分发题型：低掌握度选择题（脚手架），高掌握度回答题。"""
    qid = f"q_{entry.id}"
    if mastery < CHOICE_MASTERY_THRESHOLD:
        return _build_choice(qid, entry, distractors or [])
    return _build_answer(qid, entry)


def _build_answer(qid: str, entry: KnowledgeEntry) -> Question:
    keywords = tuple(entry.keywords[:6])
    return Question(
        question_id=qid,
        entry_id=entry.id,
        prompt=(
            f"请用自己的话解释：「{entry.title}」。"
            f"作答中尽量覆盖以下要点：{('、'.join(keywords)) or '（无）'}"
        ),
        question_type="answer",
        expected_keywords=keywords,
    )


def _build_choice(
    qid: str, entry: KnowledgeEntry, distractors: list[KnowledgeEntry]
) -> Question:
    """选择题：正确项 = 本条目关键词集；干扰项 = 其他条目关键词集（去重、取前 3）。"""
    keywords = tuple(entry.keywords[:6])
    correct_text = "、".join(keywords)
    distractors_text: list[str] = []
    seen: set[str] = set()
    for other in distractors:
        text = "、".join(other.keywords[:6])
        if not text or text == correct_text or text in seen:
            continue
        seen.add(text)
        distractors_text.append(text)
        if len(distractors_text) >= NUM_CHOICE_OPTIONS - 1:
            break

    labels = "ABCD"
    texts = [correct_text, *distractors_text]
    options = tuple(f"{labels[i]}. {t}" for i, t in enumerate(texts))
    return Question(
        question_id=qid,
        entry_id=entry.id,
        prompt=f"以下哪组要点属于「{entry.title}」的核心内容？",
        question_type="choice",
        expected_keywords=(),
        options=options,
        expected_label=labels[0],
    )


@lru_cache(maxsize=256)
def _tokens(text: str) -> tuple[str, ...]:
    """CJK 逐字 + 拉丁词切分（与 plan._tokenize 同语义，保持判分与匹配一致）。"""
    out: list[str] = []
    for ch in text.lower():
        if "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
            out.append(" ")
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append(" ")
    return tuple(t for t in "".join(out).split() if t)


def _normalize_answer(text: str) -> str:
    """作答归一化：全角字母/数字→半角（中文输入法常见）、去零宽字符与空白。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code in (0xFEFF, 0x200B, 0x200C, 0x200D):
            continue
        else:
            out.append(ch)
    return "".join(out).strip().upper()


def grade_answer(question: Question, answer: str, *, min_coverage: float = 0.6) -> GradeResult:
    """按题型判分。fail-closed：无 expected 或无作答 → 判错，绝不判对。

    - choice：作答与正确标签精确匹配（容忍大小写与空白）；
    - answer：关键词覆盖率 ≥ min_coverage。
    """
    if not answer.strip():
        return GradeResult(
            is_correct=False,
            matched=(),
            missing=question.expected_keywords,
            keyword_coverage=0.0,
            correct_label=question.expected_label,
        )

    if question.question_type == "choice":
        is_correct = _normalize_answer(answer) == question.expected_label
        return GradeResult(
            is_correct=is_correct,
            matched=(),
            missing=(),
            keyword_coverage=1.0 if is_correct else 0.0,
            correct_label=question.expected_label,
        )

    if not question.expected_keywords:
        return GradeResult(
            is_correct=False,
            matched=(),
            missing=(),
            keyword_coverage=0.0,
            correct_label=question.expected_label,
        )

    answer_tokens = set(_tokens(answer))
    answer_chars = set(answer.lower().replace(" ", ""))
    matched: list[str] = []
    for kw in question.expected_keywords:
        kw_tokens = _tokens(kw)
        if kw_tokens and all(t in answer_tokens for t in kw_tokens):
            matched.append(kw)
            continue
        kw_chars = set(re.sub(r"\s+", "", kw.lower()))
        if kw_chars and kw_chars.issubset(answer_chars):
            matched.append(kw)
    coverage = len(matched) / len(question.expected_keywords)
    missing = tuple(k for k in question.expected_keywords if k not in matched)
    return GradeResult(
        is_correct=coverage >= min_coverage,
        matched=tuple(matched),
        missing=missing,
        keyword_coverage=coverage,
        correct_label=question.expected_label,
    )


def build_feedback_message(grade: GradeResult, question: Question | None = None) -> str:
    """确定性错因反馈：缺什么要点直接点名。LLM 辅助的"教学话术"由 feedback
    节点在此消息基础上扩展，本函数不调模型、不给裁决。"""
    if grade.is_correct:
        if question is not None and question.question_type == "choice":
            return f"回答正确（{grade.correct_label}）。"
        return f"回答正确，覆盖了要点：{'、'.join(grade.matched)}。"
    if question is not None and question.question_type == "choice":
        correct_text = next(
            (o for o in question.options if o.startswith(grade.correct_label + ".")), ""
        )
        if correct_text:
            return f"回答错误。正确答案是 {correct_text}。"
        return "回答错误。"
    if not grade.missing:
        return "回答未能判为正确，请结合要点重新作答。"
    return f"回答还不够完整，缺少以下要点：{'、'.join(grade.missing)}。请对照重新作答。"


__all__ = [
    "Question",
    "GradeResult",
    "build_question",
    "grade_answer",
    "build_feedback_message",
]
