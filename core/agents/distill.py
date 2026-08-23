"""误区提炼 Agent——从学生的答错记录与脚手架素材中提炼"常见误区知识"。

定位（2026-08-23 拍板）：产出物可复用是赛题硬要求。复用的形态是把资源包
条目化导出（与知识库 entries.jsonl 同构），而**进库的是知识本身，不是
题目/脚手架原料**——错题与脚手架干扰项是提炼原料，本 agent 负责
原料 → 知识化表述 的蒸馏。

设计约束：
- 无错答素材直接短路返回空（不调 LLM，导出管线零成本）；
- 提炼物必须同域（bigram 重叠校验，防提炼出条目之外的新概念）；
- 宁缺毋滥：素材不足时 LLM 被显式允许输出空列表。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from core.agents.question import _bigram_overlap
from core.llm import LLMProvider

# 单主题误区上限：误区是讲义的补充而非主体
MAX_PITFALLS = 2

DISTILL_PROMPT = """你是教研编辑。下面是一名学习者在「{title}」主题下的真实答错记录
与教学干预素材。请从中提炼 0-2 条**常见误区知识**——不是错题记录，
而是对未来学习者可复用的知识化表述。

【主题条目】（误区涉及的概念不得超出此范围）
{entry}

【答错记录】（题目 / 学生错答 / 评估指出的问题）：
{wrong_records}

【脚手架干扰项】（教学时用于镜像典型错误理解的选项）：
{scaffold_distractors}

要求：
1. 每条误区是一句知识化表述（20-80 字），形态为"常见误区：……；正确理解是……"——
   面向未来学习者，不得出现"该学生"等会话特定指称；
2. 误区必须能从素材中明确归因（学生确实犯了此错），不得凭空添加条目外的概念；
3. 素材不足以提炼出明确误区时输出空列表——宁缺毋滥；
4. 多条误区不得互相重复。

严格按 JSON 输出：{{"pitfalls": ["...", "..."]}}"""


class DistillOutput(BaseModel):
    pitfalls: list[str] = Field(default_factory=list, description="提炼出的常见误区知识")


def validate_pitfalls(pitfalls: list[str], entry: dict[str, Any]) -> list[str]:
    """服务端校验误区文本：长度 + 同域（bigram 重叠）+ 去重 + 数量上限。

    同域校验防止提炼漂移到条目之外的概念（与 choice 题校验同源，
    二字组对齐中文词汇粒度）。任一不达标即丢弃该条——宁缺毋滥。
    """
    source = entry.get("content", "") + " " + entry.get("title", "")
    valid: list[str] = []
    seen: set[str] = set()
    for p in pitfalls or []:
        text = re.sub(r"\s+", " ", (p or "").strip())
        if not (10 <= len(text) <= 120):
            continue
        if text in seen:
            continue
        if _bigram_overlap(text, source) < 1:
            continue
        seen.add(text)
        valid.append(text)
        if len(valid) >= MAX_PITFALLS:
            break
    return valid


async def distill_pitfalls(
    state: dict[str, Any],
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> list[str]:
    """从答错记录与脚手架素材提炼误区知识。无素材短路返回空。

    state 需含：entry（{id,title,content}）、wrong_records
    （[{prompt, answer, evaluation, missed}]）、scaffold_distractors（list[str]）。
    LLM 失败返回空列表（fail-closed：误区是增量信息，缺失不影响条目主体）。
    """
    entry = state.get("entry") or {}
    wrong_records = state.get("wrong_records") or []
    scaffold_distractors = state.get("scaffold_distractors") or []
    if not wrong_records and not scaffold_distractors:
        return []
    try:
        output = await provider.chat_validated(
            [
                {
                    "role": "user",
                    "content": DISTILL_PROMPT.format(
                        title=entry.get("title", ""),
                        entry=json.dumps(
                            {
                                "id": entry.get("id"),
                                "title": entry.get("title"),
                                "content": entry.get("content"),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        wrong_records=json.dumps(wrong_records, ensure_ascii=False, indent=2),
                        scaffold_distractors=json.dumps(
                            scaffold_distractors, ensure_ascii=False
                        ),
                    ),
                }
            ],
            schema=DistillOutput,
            model=model,
            temperature=0.2,
        )
    except Exception:
        return []
    return validate_pitfalls(output.pitfalls, entry)


__all__ = ["DistillOutput", "distill_pitfalls", "validate_pitfalls"]
