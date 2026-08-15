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

from typing import Any, Literal, Protocol, TypedDict

import structlog
from pydantic import BaseModel, Field

from core.assess import Question

logger = structlog.get_logger()

FEEDBACK_PROMPT = """你是一位耐心的教学评估老师。请对学生的一道回答题作答进行评估。

【题目】{prompt}
{options_section}【学生作答】{answer}

请完成：
1. 先把题目拆成"具体要求清单"（场景、数字、方向、关键字等每个操作要求一项），
   逐项核对作答是否满足，列出未满足的要求。
2. 再判断该作答是否真正理解了知识：correct（理解正确且完整，无遗漏）/
    partial（方向对但有遗漏或偏差）/ incorrect（理解错误或答非所问）。

   判分标准（最重要——衡量"意思"，不是"用词"）：
   - 学生用实例、场景、通俗说法表达出与概念相同的含义 → 判 correct。
     不得因为未使用标准术语而判 partial/incorrect。
   - 只有出现以下情况才判 partial 或 incorrect：
     ① 概念理解错误或混淆；② 遗漏了题目明确要求回答的关键点；③ 作答与题目无关。
   - 校准示例：题目问"用什么唯一标识每个学生"，要点含"主键"。
     学生答"用学号，每个学生一个不重复" → 判 correct（学号即主键的实例，
     意思完整准确）。学生答"用姓名" → 判 incorrect（姓名可重复，概念错误）。
     学生答"用学号"但题目还明确问了第二个要求而未回答 → 判 partial（遗漏）。

3. 写一段 50-100 字的评估，要给学生看：先说学生说对/做对了什么（具体点名），
   再说遗漏或理解偏差（具体指出哪句话有问题、应该是什么），最后给出改进建议。
   语气温暖鼓励，但不放水——准确是第一位。
   若学生用非术语表达了正确理解：肯定其理解，并顺带给出规范术语
   （如"你说的'学号唯一'正是在说主键的作用"）——用词建议只进评估文本，
   不影响上面的判定。

【判分要点参考】（仅提示概念范围；学生表达出同义意思即算覆盖，非硬性词汇清单）：
{keywords}

严格按 JSON 输出：
{{"verdict": "correct|partial|incorrect", "evaluation": "评估文本",
  "missed_requirements": ["未满足的具体要求，无则空列表"]}}"""


class FeedbackOutput(BaseModel):
    verdict: Literal["correct", "partial", "incorrect"] = Field(description="LLM 判分复核结果")
    evaluation: str = Field(description="给学生看的教学评估文本")
    missed_requirements: list[str] = Field(
        default_factory=list, description="题目要求中作答未满足的清单（无则空）"
    )


class FeedbackLLM(Protocol):
    """feedback 需要的 LLM 最小接口：测试可用 Fake 注入。"""

    async def chat_validated(self, messages, schema, model=None, **kwargs) -> Any: ...


class FeedbackInput(TypedDict, total=False):
    """feedback 输入：question（Question 序列化 dict）、学生作答、规则判分结果。"""

    question: dict[str, Any]
    answer: str
    rule_is_correct: bool
    rule_coverage: float


async def feedback_node(
    state: FeedbackInput,
    *,
    provider: FeedbackLLM,
    model: str | None = None,
) -> dict:
    """对当前作答做 LLM 复核 + 教学评估。

    state 需含：question（Question 序列化）、answer（学生作答）、
    rule_is_correct（规则判分结果）、rule_coverage（规则覆盖率）。
    返回 verdict / evaluation 两个字段。
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
        # fail-closed：LLM 缺席时没有"理解质量"的证据——按更严标准回退：
        # 要求关键词全覆盖（coverage=1.0）才判对，否则判 incorrect（retry）。
        # 规则的低阈值（0.6）是 LLM 复核存在时的预筛，不能单独作为放行依据。
        coverage = float(state.get("rule_coverage", 0.0))
        logger.warning(
            "feedback_llm_failed_fallback_rule",
            error=str(exc)[:120],
            coverage=coverage,
        )
        return {
            "verdict": "correct" if coverage >= 1.0 else "incorrect",
            "evaluation": "",
            "missed_requirements": [],
        }

    # 服务端矛盾检测：判 correct 却自报遗漏清单 → 硬降级为 partial。
    # 规则覆盖率可能因 expected 不全而虚高，LLM 的遗漏清单是题意核对证据。
    verdict = output.verdict
    if verdict == "correct" and output.missed_requirements:
        verdict = "partial"
    return {
        "verdict": verdict,
        "evaluation": output.evaluation,
        "missed_requirements": output.missed_requirements,
    }


__all__ = ["FeedbackOutput", "feedback_node"]
