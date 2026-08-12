"""题目生成 Agent——回答题题干与判分要点一起由 LLM 生成，服务端校验。

设计（2026-08-11 拍板，修复"题目与判分脱节"缺陷）：
- LLM 生成 {question, expected_keywords}——题目问什么，判分就看什么；
- **服务端校验**：每个 expected 要点的字符必须全部出现在条目 content 中
  （复用 test_seeds 同款字符子集校验）——LLM 编造的超纲要点直接丢弃，
  全部校验失败则回退条目原始 keywords（fail-closed）；
- 校验通过的 expected 才进判分，按 entry_id 与题干一起缓存；
- 判定"测什么"的最终权威在服务端规则，LLM 只提供候选。

约束：题干必须围绕条目内容；expected 数量 1-4 个（防止过严/过宽）。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from core.llm import LLMProvider

# 判分要点数量上限：过多则覆盖率判定过严（答对一题要全说才 60%）。
# 题目含多个操作要求时（如"前5件+降序+名称升序"）需要更多要点，放宽到 5。
MAX_EXPECTED_KEYWORDS = 5

QUESTION_PROMPT = """你是一位经验丰富的培训讲师。请为下面这个知识条目设计一道引导性的回答题，
并列出学生回答这道题时必须覆盖的要点。

【知识条目】
{entry}

【本轮教学内容】（出题深度契约：题目只能测这些已教过的内容）
{claims}

【题目设计要求】
1. 用具体、场景化的方式提问，引导学生思考概念的含义、作用与联系——
   不要用"请解释一下X"这种泛泛的问法。
2. 可以结合数据分析实际场景提问（如："统计报表里出现重复的行，你会怎么解决？"）。
3. 问题必须只基于【本轮教学内容】中的论断提问——学生只需运用本轮已教的概念
   就能作答；禁止问本轮教学中未覆盖的知识，禁止要求超出教学内容的理解深度。
4. 问题长度 30-80 字，中文，一段话。
5. 不要给出答案，只输出问题。

【判分要点要求】
1. expected_keywords：2-5 个，必须是学生回答这道题时需要提到的关键概念或术语。
2. 每个要点都必须能从条目的 content 原文中找到对应内容——禁止编造条目里没有的概念。
3. 要点要贴合你的题目：题目问什么，要点就是答什么需要的。
4. **题目中的每个具体操作要求都必须对应一个要点**——比如"前5件商品"→LIMIT、
   "按销量降序"→DESC、"相同再按名称升序"→ASC。宁多勿漏：遗漏的要点在判分时
   无法被检查，学生漏答也会被判对。

严格按 JSON 输出：
{{"question": "问题文本", "expected_keywords": ["要点1", "要点2"]}}"""


class QuestionOutput(BaseModel):
    question: str = Field(description="引导性回答题题干")
    expected_keywords: list[str] = Field(
        default_factory=list, description="回答此题需覆盖的判分要点"
    )


class ScaffoldOutput(BaseModel):
    distractors: list[str] = Field(
        default_factory=list, description="脚手架选择题干扰项（2-3 个，首项为学生错误理解镜像）"
    )


def validate_distractors(distractors: list[str], correct_text: str, max_items: int = 3) -> list[str]:
    """服务端校验脚手架干扰项：去重、排除与正确项相同文本、数量上限 3。

    校验通过的才可进选项——LLM 生成的干扰项与正确项撞车会被丢弃。
    """
    valid: list[str] = []
    seen: set[str] = set()
    for d in distractors or []:
        text = (d or "").strip()
        if not text or text == correct_text:
            continue
        if text in seen:
            continue
        seen.add(text)
        valid.append(text)
        if len(valid) >= max_items:
            break
    return valid


class QuestionInput(TypedDict, total=False):
    entry: dict[str, Any]
    taught_claims: list[str]  # 本轮教学论断文本（出题深度契约）


SCAFFOLD_PROMPT = """你是一位培训讲师。学生在上一道回答题中答得不好，请为该知识点设计
一道选择题脚手架的干扰项，帮助学生通过对比选项发现自己的问题。

【上一道回答题】{failed_question}
【学生作答】{student_answer}
【正确选项内容】（服务端已定，请勿改动）{correct_text}

请生成 2-3 个干扰项（错误选项）：
1. 第 1 个干扰项必须从学生作答中提炼其"典型错误理解"（若作答有明显错误理解）；
   若只是遗漏要点，则生成一个贴近主题但错误的说法。
2. 其余干扰项为常见误解或相关但错误的概念。
3. 每个干扰项 4-30 字，不得与正确选项内容相同。

严格按 JSON 输出：
{{"distractors": ["干扰项1", "干扰项2"]}}"""


async def build_scaffold_distractors(
    provider,
    failed_question: str,
    student_answer: str,
    correct_text: str,
    *,
    model: str | None = None,
) -> list[str]:
    """为脚手架选择题生成干扰项（含学生错误理解镜像）。

    失败时返回空列表，由调用方回退确定性干扰项（fail-closed）。
    """
    output = await provider.chat_validated(
        [
            {
                "role": "user",
                "content": SCAFFOLD_PROMPT.format(
                    failed_question=failed_question,
                    student_answer=student_answer,
                    correct_text=correct_text,
                ),
            }
        ],
        schema=ScaffoldOutput,
        model=model,
    )
    return validate_distractors(output.distractors, correct_text)


def validate_expected_keywords(keywords: list[str], content: str) -> list[str]:
    """服务端校验判分要点：字符必须全部出自条目 content，数量 1-4。

    校验通过的才可进判分——LLM 编造的超纲要点被丢弃。
    """
    content_chars = set(re.sub(r"\s+", "", content or "").lower())
    valid: list[str] = []
    for kw in keywords or []:
        text = (kw or "").strip()
        if not text:
            continue
        chars = set(re.sub(r"\s+", "", text.lower()))
        if chars and chars.issubset(content_chars):
            valid.append(text)
        if len(valid) >= MAX_EXPECTED_KEYWORDS:
            break
    return valid


async def question_node(
    state: QuestionInput,
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> dict:
    """生成回答题题干 + 判分要点（服务端校验后返回）。

    state 可含 entry（知识条目）与 taught_claims（本轮教学论断文本列表，
    出题深度契约：题目只能测已教过的内容）。expected 校验失败返回空列表，
    由调用方回退条目原始 keywords。
    """
    entry = state.get("entry")
    if entry is None:
        return {"question": "", "expected_keywords": []}
    entry_text = json.dumps(
        {
            "id": entry["id"],
            "title": entry["title"],
            "content": entry["content"],
            "keywords": entry.get("keywords", []),
        },
        ensure_ascii=False,
        indent=2,
    )
    claims = state.get("taught_claims", [])
    claims_text = (
        "\n".join(f"- {c}" for c in claims)
        if claims
        else "（未提供——若本轮无教学内容，请基于条目内容提问，深度以条目原文为上限）"
    )
    output = await provider.chat_validated(
        [{"role": "user", "content": QUESTION_PROMPT.format(entry=entry_text, claims=claims_text)}],
        schema=QuestionOutput,
        model=model,
    )
    valid = validate_expected_keywords(output.expected_keywords, entry.get("content", ""))
    return {"question": output.question, "expected_keywords": valid}


__all__ = [
    "QuestionOutput",
    "ScaffoldOutput",
    "validate_expected_keywords",
    "validate_distractors",
    "build_scaffold_distractors",
    "question_node",
]
