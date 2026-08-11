"""assess 与 feedback 的确定性核心——出题结构、判分、错因提取。

评分模式（2026-07-22 拍板）：确定性为主 + LLM 辅助——规则判对错（可复现、
可追溯），LLM 只负责把错因翻译成教学语言（在 feedback 节点接线，本模块不给
LLM 留任何裁决权）。

题型随知识类型扩展（当前条目无 knowledge_type 字段，第一版统一复述题；
字段上线后按类型切分题型，见 build_question 的 TODO）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from core.plan import KnowledgeEntry


@dataclass(frozen=True)
class Question:
    """一道确定性考题：题干由条目生成，expected 是判分依据（服务端持有）。

    对齐 DeepTutor PendingQuestion 模式：expected 永不进学生视野，判分
    fail-closed——没有 expected 就判错，绝不判对。
    """

    question_id: str
    entry_id: str
    prompt: str
    question_type: str  # "recall" 复述题（第一版统一题型）
    expected_keywords: tuple[str, ...]


@dataclass(frozen=True)
class GradeResult:
    is_correct: bool
    matched: tuple[str, ...]  # 学生作答中命中的关键词
    missing: tuple[str, ...]  # 应覆盖但未命中的关键词
    keyword_coverage: float


def build_question(entry: KnowledgeEntry) -> Question:
    """从条目生成复述题。题干直接使用条目主题，expected 取条目关键词。

    TODO(knowledge_type)：条目 schema 增加类型后，memory → 选择题、
    procedure → 操作题、concept → 复述题；本函数按类型分发。
    """
    keywords = tuple(entry.keywords[:6])
    return Question(
        question_id=f"q_{entry.id}",
        entry_id=entry.id,
        prompt=(
            f"请用自己的话解释：「{entry.title}」。"
            f"作答中尽量覆盖以下要点：{('、'.join(keywords)) or '（无）'}"
        ),
        question_type="recall",
        expected_keywords=keywords,
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


def grade_answer(question: Question, answer: str, *, min_coverage: float = 0.6) -> GradeResult:
    """关键词覆盖率判分。fail-closed：无 expected 关键词或无作答 → 判错。

    覆盖率 = 命中关键词数 / expected 关键词数。一个关键词的所有字符全部
    出现在作答中即算命中（CJK 逐字），拉丁词按整词命中。
    """
    if not answer.strip() or not question.expected_keywords:
        return GradeResult(
            is_correct=False,
            matched=(),
            missing=question.expected_keywords,
            keyword_coverage=0.0,
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
    )


def build_feedback_message(grade: GradeResult) -> str:
    """确定性错因反馈：缺什么要点直接点名。LLM 辅助的"教学话术"由 feedback
    节点在此消息基础上扩展，本函数不调模型、不给裁决。"""
    if grade.is_correct:
        return f"回答正确，覆盖了要点：{'、'.join(grade.matched)}。"
    if not grade.missing:
        return "回答未能判为正确，请结合要点重新作答。"
    return f"回答还不够完整，缺少以下要点：{'、'.join(grade.missing)}。请对照重新作答。"


__all__ = ["Question", "GradeResult", "build_question", "grade_answer", "build_feedback_message"]
