"""误区提炼 Agent——从学生的答错记录与脚手架素材中提炼"常见误区知识"。

定位（2026-08-23 拍板）：产出物可复用是赛题硬要求。复用的形态是把资源包
条目化导出（与知识库 entries.jsonl 同构），而**进库的是知识本身，不是
题目/脚手架原料**——错题与脚手架干扰项是提炼原料，本 agent 负责
原料 → 知识化表述 的蒸馏。

设计原则（2026-08-27 二次重审）：**质量第一责任在生成侧**——上下文管控
（只喂必要事实原料 + 讲义锚点）+ 提示词硬性约束（单一知识点/完全通用化/
锚定取材），一次 LLM 调用直接产出合格结果；`validate_pitfalls` 只是安全
兜底，不用下游过滤弥补上游提示词的不足。

输入侧管控：
- 给三样、不给一样：
  1. **事实原料**——题干/作答/遗漏要点。评估文本（evaluation）不进上下文：
     那是面向当前学生的第二人称个性化措辞，喂进去会被复读（实测泄漏源）；
  2. **讲义锚点**——本主题知识化过滤后的讲义论断，"正确理解"的唯一取材源；
  3. **条目原文**——概念范围闸门。

输出侧约束（提示词硬性约束 A-F，校验仅作兜底）：
- 每条误区只针对一个独立知识点（禁止混合概念）；
- 完全通用化（禁第二人称/敬语/画像背景/作答原文）；
- 锚定讲义：挂 evidence_ids，服务端兜底校验与所引论断 bigram 重叠 ≥ 阈值；
- 领域语言限定：术语只取自条目/讲义，禁借学习者行业场景词。

无错答素材直接短路返回空（不调 LLM，导出管线零成本）；宁缺毋滥。
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

# 误区与所引讲义论断的 bigram 重叠阈值（兜底校验用）：正确理解必须取材自讲义
# 而非凭空编造。误区句含"错误说法"半句天然拉低重叠度，故阈值不高——但必须锚上。
EVIDENCE_OVERLAP_MIN = 2

DISTILL_PROMPT = """你是教研编辑，正在为知识库编写可复用的"常见误区"条目。下面是学习者
在「{title}」主题下的真实答错记录、当时使用的教学讲义与脚手架素材。
请提炼 0-2 条**常见误区知识**。以下要求是硬性约束，违反任何一条即不合格（A-F）：

【硬性约束】
A. **单一知识点**：每条误区只针对一个独立知识点——一条误区里禁止混合多个概念，
   禁止用分号/顿号并列多组"误区+纠正"；
B. **完全通用化**：面向未来所有学习者。严禁第二人称与敬语（"你/您"），
   严禁指称具体学习者（"该学生/该学员"），严禁引用任何学习者的专业背景、
   作答原文或个人情境——误区与"谁犯了错"无关，只与"错在哪"有关；
C. **锚定条目与讲义**：误区涉及的概念不得超出【主题条目】范围；"正确理解是……"
   部分必须从【教学讲义】取材，并在 evidence_ids 标注支撑论断的序号；
   讲义中没有能支撑的内容就不要输出这条；
D. **可归因且优先高价值困惑**：每条误区必须对应素材中真实发生的错误（答错记录、
   脚手架干扰项或追问确认题），不得凭空添加；若错因根源在源条目表述过于简略、
   或概念上直觉易混淆（学习者因此困惑），优先提炼这类误区——它们对未来学习者
   最可复用；
E. **宁缺毋滥**：素材不足以提炼出明确误区时输出空列表；多条误区不得互相重复；
F. **领域语言限定**：只使用【主题条目】与【教学讲义】中出现的专业术语与表述；
   严禁借用学习者自身行业的场景词汇（如混凝土/机械/物流等）；
   若需场景辅助，只用领域内中性通用场景（如数据表/记录/成绩）——
   可复用知识不带任何行业色彩。

【主题条目】（概念范围闸门）
{entry}

【教学讲义】（编号为论断序号，即 evidence_ids 的合法取值；
"正确理解"的唯一取材来源）：
{claims}

【答错记录】（题干 / 学习者错答 / 遗漏的题目要求；其中学习者主动提出的追问
以其题干呈现、作答为空——那是学习者自发暴露的困惑点，高价值原料）：
{wrong_records}

【脚手架干扰项】（教学时用于镜像典型错误理解的选项）：
{scaffold_distractors}

表述规格：每条一句知识化表述（20-80 字），形态固定为"常见误区：……；正确理解是……"，
只输出误区表述本身，不复述讲义原文、不做点评。

严格按 JSON 输出：{{"pitfalls": [{{"text": "……", "evidence_ids": [0, 2]}}]}}"""


class PitfallItem(BaseModel):
    text: str = Field(description="误区知识表述（20-80 字，单一知识点，无学习者指涉）")
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
    """服务端兜底校验：长度 + 无学习者指涉/敬语 + 证据锚定（或同域）+ 去重 + 上限。

    定位是安全网而非质量主力——合格产出由提示词硬性约束（单一知识点/通用化/
    锚定取材）在生成侧保证，本函数只拦漏网与恶意输出：
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
        # 兜底拦截：学习者指涉 + 第二人称敬语（误区的读者是未来学习者，"您"属对话措辞）
        if has_personal_reference(text) or "您" in text:
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
    （[{prompt, answer, missed}]——只收事实原料，评估文本不进上下文，
    防个性化措辞被复读）、scaffold_distractors（list[str]）；
    建议含 taught_claims（讲义论断，"正确理解"的取材锚点；缺失时校验
    退回条目同域）。质量由提示词硬性约束在生成侧保证，校验只兜底。
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
