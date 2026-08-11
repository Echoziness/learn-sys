"""feedback——LLM 判分复核与教学评估。

评分模式（2026-07-22 拍板）：确定性为主 + LLM 辅助。规则层负责 fail-closed
快路径（覆盖率不达标直接判错，不调 LLM），LLM 负责两件事：

1. 判分复核：覆盖率达标的作答未必体现理解（可能答非所问、逻辑错误），
   由 LLM 裁决是否真正正确——裁决权从规则移交，但规则仍是兜底；
2. 教学评估：无论对错，产出学生能看到的评估文本（肯定要点、指出遗漏、
   纠正理解偏差），这是"学生至少要看到评估"的落地。

fail-closed：LLM 调用失败/超时时回退规则判定与规则反馈消息，绝不判对。

上下文隔离：feedback 只读 题目/要点/作答/规则判分结果——不接触画像。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

import structlog
from pydantic import BaseModel, Field

from core.assess import Question
from core.llm import LLMProvider

logger = structlog.get_logger()

FEEDBACK_PROMPT = """你是一位耐心的教学评估老师。请对学生的一道回答题作答进行评估。

题目：{prompt}
{options_section}判分要点（expected）：{keywords}
学生作答：{answer}

请完成：
1. 判断该作答是否真正理解了知识：correct（理解正确且完整）/
   partial（方向对但有遗漏或偏差）/ incorrect（理解错误或答非所问）。
   - 注意：作答覆盖了关键词但逻辑错误、概念混淆、编造内容时，必须判 incorrect 或 partial，不能只看关键词。
2. 写一段 50-100 字的评估，要给学生看：先说学生说对/做对了什么（具体点名），
   再说遗漏或理解偏差（具体指出哪句话有问题、应该是什么），最后给出改进建议。
   语气温暖鼓励，但不放水——准确是第一位。

严格按 JSON 输出：
{{"verdict": "correct|partial|incorrect", "evaluation": "评估文本"}}"""


class FeedbackOutput(BaseModel):
    verdict: Literal["correct", "partial", "incorrect"] = Field(description="LLM 判分复核结果")
    evaluation: str = Field(description="给学生看的教学评估文本")


class FeedbackInput(TypedDict, total=False):
    """feedback 输入：question（Question 序列化 dict）、学生作答、规则判分结果。"""

    question: dict[str, Any]
    answer: str
    rule_is_correct: bool


async def feedback_node(
    state: FeedbackInput,
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> dict:
    """对当前作答做 LLM 复核 + 教学评估。

    state 需含：question（Question 序列化）、answer（学生作答）、
    rule_is_correct（规则判分结果）。返回 verdict / evaluation 两个字段。
    """
    question_raw = state.get("question")
    answer = state.get("answer", "")
    if question_raw is None:
        return {"verdict": "partial", "evaluation": "（无题目信息，无法评估）"}
    question = Question(**question_raw)

    try:
        output = await provider.chat_validated(
            [
                {
                    "role": "user",
                    "content": FEEDBACK_PROMPT.format(
                        prompt=question.prompt,
                        options_section=(
                            "选项：\n" + "\n".join(question.options) + "\n"
                            if question.options
                            else ""
                        ),
                        keywords="、".join(question.expected_keywords) or "（选择题，仅需判选项理解）",
                        answer=answer,
                    ),
                }
            ],
            schema=FeedbackOutput,
            model=model,
        )
    except Exception as exc:
        # fail-closed：LLM 不可用时回退规则判定，评估文本由规则消息兜底。
        logger.warning("feedback_llm_failed_fallback_rule", error=str(exc)[:120])
        rule_ok = bool(state.get("rule_is_correct", False))
        return {
            "verdict": "correct" if rule_ok else "incorrect",
            "evaluation": "",
        }

    return {"verdict": output.verdict, "evaluation": output.evaluation}


__all__ = ["FeedbackOutput", "feedback_node"]
