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
    missed_requirements: tuple[str, ...] = ()  # 题意核对：未满足的题目要求（审计用）


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

    评估与裁决分离（2026-08-12；2026-08-15 收口修订）：
    - answer 题总是送 LLM 评估——覆盖率不足的学生作答往往最需要
      教学点评（概念错误比"答非所问"更有教学价值），规则预筛只降级裁决；
    - 裁决权归属（同义表达判定的结构基础）：关键词覆盖是字符级代理指标，
      测不了"用实例表达同义"——LLM 判 correct 且题意核对无遗漏时采纳判定。
      防放水不靠规则否决，靠三层：矛盾检测（feedback_node 内 correct+missed
      → 硬降 partial）、题意核对清单（missed_requirements 必须逐项真实）、
      后续轮兜底（题型单向推进 + advance 需多轮 mastery 门槛 + regress）。
      规则覆盖率继续落 grade 作审计信号。
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
    fb: dict = {}
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
        missed_requirements=tuple(fb.get("missed_requirements", []) if llm_reviewed else ()),
    )


__all__ = ["AnswerOutcome", "process_answer"]
