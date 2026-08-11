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
MAX_EXPECTED_KEYWORDS = 4

QUESTION_PROMPT = """你是一位经验丰富的培训讲师。请为下面这个知识条目设计一道引导性的回答题，
并列出学生回答这道题时必须覆盖的要点。

【知识条目】
{entry}

【题目设计要求】
1. 用具体、场景化的方式提问，引导学生思考概念的含义、作用与联系——
   不要用"请解释一下X"这种泛泛的问法。
2. 可以结合数据分析实际场景提问（如："统计报表里出现重复的行，你会怎么解决？"）。
3. 问题必须只基于条目内容，禁止问条目中不存在的知识点。
4. 问题长度 30-80 字，中文，一段话。
5. 不要给出答案，只输出问题。

【判分要点要求】
1. expected_keywords：2-4 个，必须是学生回答这道题时需要提到的关键概念或术语。
2. 每个要点都必须能从条目的 content 原文中找到对应内容——禁止编造条目里没有的概念。
3. 要点要贴合你的题目：题目问什么，要点就是答什么需要的。

严格按 JSON 输出：
{{"question": "问题文本", "expected_keywords": ["要点1", "要点2"]}}"""


class QuestionOutput(BaseModel):
    question: str = Field(description="引导性回答题题干")
    expected_keywords: list[str] = Field(
        default_factory=list, description="回答此题需覆盖的判分要点"
    )


class QuestionInput(TypedDict, total=False):
    entry: dict[str, Any]


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


async def question_node(state: QuestionInput, *, provider: LLMProvider, model: str | None = None) -> dict:
    """生成回答题题干 + 判分要点（服务端校验后返回）。

    返回 {"question": str, "expected_keywords": [...]}。expected 校验失败
    返回空列表，由调用方回退条目原始 keywords。
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
    output = await provider.chat_validated(
        [{"role": "user", "content": QUESTION_PROMPT.format(entry=entry_text)}],
        schema=QuestionOutput,
        model=model,
    )
    valid = validate_expected_keywords(output.expected_keywords, entry.get("content", ""))
    return {"question": output.question, "expected_keywords": valid}


__all__ = ["QuestionOutput", "validate_expected_keywords", "question_node"]
