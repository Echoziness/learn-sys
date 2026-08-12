"""作答处理管线——CLI 与未来 Web 端共用的唯一入口。

一次"学生作答"的完整处理链：规则判分 → （必要时）LLM 复核 + 教学评估
→ 判定对错 → 更新掌握度 → 给出进/停/退决策。

设计（2026-08-11）：CLI 是开发自测工具，Web 是主战场——判分/反馈/决策
逻辑不能散在 CLI 里，抽成服务函数，FastAPI 后续直接调用同一入口。
纯 async，无 I/O 副作用（掌握度计算是纯函数，由调用方持有历史并回写）。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.agents.feedback import FeedbackLLM, feedback_node
from core.assess import GradeResult, Question, build_feedback_message, grade_answer
from core.mastery import NextStep, decide_next_step


@dataclass(frozen=True)
class AnswerOutcome:
    """一次作答的完整结果：CLI 展示与 Web 序列化共用。"""

    grade: GradeResult
    is_correct: bool  # 最终判定（规则 + LLM 复核）
    evaluation: str  # 给学生看的评估文本（LLM 或规则兜底）
    mastery: float  # 更新后的掌握度
    decision: NextStep  # advance / retry / regress
    attempts: int  # 该主题累计作答次数
    llm_reviewed: bool  # 是否经过 LLM 复核（false = 规则快路径/兜底）


async def process_answer(
    provider: FeedbackLLM,
    question: Question,
    answer: str,
    correctness_history: list[bool],
    *,
    model: str | None = None,
    min_coverage: float = 0.6,
) -> AnswerOutcome:
    """处理一次作答：判分 → 复核/评估 → 更新掌握度 → 决策。

    correctness_history 为调用方持有的该主题历史（本函数只读，
    返回的 decision 依赖追加本回合结果后的完整历史）。

    评估与裁决分离（2026-08-12）：
    - answer 题总是送 LLM 评估——覆盖率不足的学生作答往往最需要
      教学点评（概念错误比"答非所问"更有教学价值），规则预筛只降级裁决；
    - fail-closed 收口：LLM 判 correct 但规则覆盖率不足（< min_coverage）时
      维持判错——关键词覆盖是放行的底线，LLM 无权绕过（防 LLM 放水）。
    """
    grade = grade_answer(question, answer, min_coverage=min_coverage)

    # LLM 复核触发条件：answer 题总是复核（评估价值 > 裁决价值）；
    # choice 题仅在答错时（需要解释错因）。
    is_correct = grade.is_correct
    evaluation = build_feedback_message(grade, question)
    need_llm = question.question_type == "answer" or (
        question.question_type == "choice" and not grade.is_correct
    )
    llm_reviewed = False
    if need_llm:
        fb = await feedback_node(
            {
                "question": {
                    k: v
                    for k, v in (
                        ("question_id", question.question_id),
                        ("entry_id", question.entry_id),
                        ("prompt", question.prompt),
                        ("question_type", question.question_type),
                        ("expected_keywords", list(question.expected_keywords)),
                        ("options", list(question.options)),
                        ("expected_label", question.expected_label),
                    )
                },
                "answer": answer,
                "rule_is_correct": grade.is_correct,
                "rule_coverage": grade.keyword_coverage,
            },
            provider=provider,
            model=model,
        )
        llm_reviewed = True
        verdict_correct = fb["verdict"] == "correct"
        if question.question_type == "answer" and verdict_correct and grade.keyword_coverage < min_coverage:
            # fail-closed：覆盖不足时 LLM 无权放行，维持规则判错；
            # 评估也不采用 LLM 的（避免"答对了"的误导性反馈）。
            is_correct = False
        else:
            is_correct = verdict_correct
            if fb["evaluation"]:
                evaluation = fb["evaluation"]

    history = [*correctness_history, is_correct]
    decision, mastery = decide_next_step(history)
    return AnswerOutcome(
        grade=grade,
        is_correct=is_correct,
        evaluation=evaluation,
        mastery=mastery,
        decision=decision,
        attempts=len(history),
        llm_reviewed=llm_reviewed,
    )


__all__ = ["AnswerOutcome", "process_answer"]
