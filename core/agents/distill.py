"""误区提炼 Agent——从学生的答错记录与脚手架素材中提炼"常见误区知识"。

定位（2026-08-23 拍板）：产出物可复用是赛题硬要求。复用的形态是把资源包
条目化导出（与知识库 entries.jsonl 同构），而**进库的是知识本身，不是
题目/脚手架原料**——错题与脚手架干扰项是提炼原料，本 agent 负责
原料 → 知识化表述 的蒸馏。

输入/输出本质（2026-08-27 重审重做）：
- 给三样输入：
  1. **事实原料**——题干/学生错答/遗漏要点。评估文本（evaluation）不进
     上下文：那是面向当前学生的第二人称个性化措辞，喂进去会被复读到
     可复用知识里（实测泄漏源之一）；
  2. **正确锚点**——本主题审核通过的讲义论断。"正确理解是……"必须从
     这里取材，不靠 LLM 凭印象编；
  3. **条目原文**——概念范围闸门。
- 输出是**锚定证据的误区**：每条挂 evidence_ids（支撑"正确理解"的讲义
  论断序号），服务端强校验——误区与所引论断 bigram 重叠 ≥ 阈值才保留，
  锚不上即丢弃（"正确理解"无出处 = 编造，与生成-审核证据链同口径）。
- 无错答素材直接短路返回空（不调 LLM，导出管线零成本）；宁缺毋滥。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from core.agents.question import _bigram_overlap
from core.llm import LLMProvider

# 单主题误区上限：误区是讲义的补充而非主体（讲义已含全部知识，误区只补认知陷阱）
MAX_PITFALLS = 2

# 误区与所引讲义论断的 bigram 重叠阈值：正确理解必须取材自讲义而非凭空编造。
# 误区句含"错误说法"半句天然拉低重叠度，故阈值不高——但必须能锚上。
EVIDENCE_OVERLAP_MIN = 2

DISTILL_PROMPT = """你是教研编辑。下面是学习者在「{title}」主题下的真实答错记录、
当时使用的教学讲义与脚手架素材。请提炼 0-2 条**常见误区知识**——面向未来所有
学习者的、可复用的知识表述，不是针对某个具体学生的错题点评。
每条误区必须有讲义出处：evidence_ids 填能支撑其"正确理解"部分的讲义论断序号；
若讲义中没有能支撑的内容，就不要输出这条——宁缺毋滥。

【主题条目】（概念范围闸门，误区不得超出此范围）
{entry}

【教学讲义】（编号为论断序号，也是 evidence_ids 的合法取值；
这是你写"正确理解是……"的唯一取材来源）：
{claims}

【答错记录】（题干 / 学生错答 / 遗漏的题目要求）：
{wrong_records}

【脚手架干扰项】（教学时用于镜像典型错误理解的选项）：
{scaffold_distractors}

要求：
1. 每条误区是一句知识化表述（20-80 字），形态"常见误区：……；正确理解是……"；
2. **绝对禁止**提及任何具体学习者：不得出现"你/该学生/该学员/对于…而言"等指涉，
   不得引用学习者的专业背景或作答原文——误区与具体学习者无关；
3. 误区必须能从素材中明确归因（确实有学习者犯了此错），
   "正确理解"部分必须从教学讲义取材（并在 evidence_ids 标注出处）；
4. 素材不足以提炼出明确误区时输出空列表——宁缺毋滥；多条误区不得互相重复。

严格按 JSON 输出：{{"pitfalls": [{{"text": "……", "evidence_ids": [0, 2]}}]}}"""


class PitfallItem(BaseModel):
    text: str = Field(description="误区知识表述（20-80 字，无学习者指涉）")
    evidence_ids: list[int] = Field(
        default_factory=list, description="支撑'正确理解'的讲义论断序号"
    )


class DistillOutput(BaseModel):
    pitfalls: list[PitfallItem] = Field(default_factory=list, description="提炼出的常见误区知识")


def validate_pitfalls(
    pitfalls: list[PitfallItem] | list[str],
    entry: dict[str, Any],
    claims: list[dict[str, Any]] | None = None,
) -> list[str]:
    """服务端校验误区：长度 + 无学习者指涉 + 证据锚定（或同域）+ 去重 + 上限。

    双层防漂移：
    - 给了讲义论断（正常路径）：误区与所引论断的 bigram 重叠须 ≥ 阈值——
      锚不上讲义即丢弃（"正确理解"无出处，防幻觉同口径）；
    - 无讲义（兼容旧调用/降级）：退回条目同域校验（与 choice 题校验同源）。
    任一不达标即丢弃该条——宁缺毋滥。
    """
    from core.deliver import has_personal_reference  # 延迟导入：避免 deliver↔agents 互引

    source = entry.get("content", "") + " " + entry.get("title", "")
    valid: list[str] = []
    seen: set[str] = set()
    for item in pitfalls or []:
        # 兼容裸字符串输入（历史数据/旧调用方）：无结构即无锚定，走同域校验
        if isinstance(item, str):
            text, ev_ids = item, []
        else:
            text, ev_ids = item.text, item.evidence_ids
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not (10 <= len(text) <= 120):
            continue
        if has_personal_reference(text):
            continue
        if text in seen:
            continue
        if claims:
            anchors = [
                str(claims[i].get("text", ""))
                for i in ev_ids
                if isinstance(i, int) and 0 <= i < len(claims)
            ]
            if not anchors or max(_bigram_overlap(text, a) for a in anchors) < EVIDENCE_OVERLAP_MIN:
                continue
        elif _bigram_overlap(text, source) < 1:
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
    （[{prompt, answer, missed}]——评估文本不进上下文，防个性化措辞被复读）、
    scaffold_distractors（list[str]）；建议含 taught_claims（讲义论断，
    "正确理解"的取材锚点；缺失时退回条目同域校验）。
    LLM 失败返回空列表（fail-closed：误区是增量信息，缺失不影响条目主体）。
    """
    entry = state.get("entry") or {}
    wrong_records = state.get("wrong_records") or []
    scaffold_distractors = state.get("scaffold_distractors") or []
    claims = state.get("taught_claims") or []
    if not wrong_records and not scaffold_distractors:
        return []
    claims_section = "\n".join(f"[{i}] {c.get('text', '')}" for i, c in enumerate(claims))
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
                        claims=claims_section or "（无）",
                        wrong_records=json.dumps(wrong_records, ensure_ascii=False, indent=2),
                        scaffold_distractors=json.dumps(scaffold_distractors, ensure_ascii=False),
                    ),
                }
            ],
            schema=DistillOutput,
            model=model,
            temperature=0.2,
        )
    except Exception:
        return []
    return validate_pitfalls(output.pitfalls, entry, claims)


__all__ = [
    "DISTILL_PROMPT",
    "DistillOutput",
    "EVIDENCE_OVERLAP_MIN",
    "PitfallItem",
    "distill_pitfalls",
    "validate_pitfalls",
]
