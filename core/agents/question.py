"""题目生成 Agent——回答题题干由 LLM 生成，判分要点仍由规则派生。

设计（2026-08-11 拍板）：模板题干"请用自己的话解释「X」"过于宽泛，
学生不知道该解释到什么深度、从什么角度，容易变成关键词罗列。

分离原则的又一次应用：
- **测什么**（expected_keywords）由规则从条目派生，LLM 无权改动——判分确定性不变；
- **怎么问**（题干文本）由 LLM 基于条目内容生成——更具体、有引导性、贴近真实提问。

约束：
- 题干必须围绕条目内容（prompt 明令禁止问条目之外的知识）；
- LLM 失败/超时 → 调用方回退确定性模板（fail-closed，绝不给出无校验的题目）；
- 同一条目的题干按 entry_id 缓存，多次教学复用（可复现、省调用）。
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from core.llm import LLMProvider

QUESTION_PROMPT = """你是一位经验丰富的培训讲师。请为下面这个知识条目设计一道引导性的回答题。

【知识条目】
{entry}

【题目设计要求】
1. 用具体、场景化的方式提问，引导学生思考概念的含义、作用与联系——
   不要用"请解释一下X"这种泛泛的问法。
2. 可以结合数据分析实际场景提问（如："统计报表里出现重复的行，你会怎么解决？"）。
3. 问题必须只基于条目内容，禁止问条目中不存在的知识点。
4. 问题长度 30-80 字，中文，一段话。
5. 不要给出答案，只输出问题。

严格按 JSON 输出：{{"question": "问题文本"}}"""


class QuestionOutput(BaseModel):
    question: str = Field(description="引导性回答题题干")


class QuestionInput(TypedDict, total=False):
    entry: dict[str, Any]


async def question_node(state: QuestionInput, *, provider: LLMProvider, model: str | None = None) -> dict:
    """生成回答题题干。返回 {"question": str}；异常由调用方回退模板。"""
    entry = state.get("entry")
    if entry is None:
        return {"question": ""}
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
    return {"question": output.question}


__all__ = ["QuestionOutput", "question_node"]
