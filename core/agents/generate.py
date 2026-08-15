"""知识生成 Agent。仅接收检索条目 + 画像摘要 + 大纲 + 上轮反馈——其余 state 不可访问。"""

import json

import structlog

from core.llm import LLMProvider
from core.state import AgentState, GenerateOutput

logger = structlog.get_logger()

# 按学习者水平的难度提示——结构化硬约束，而非软建议
DIFFICULTY_INSTRUCTION: dict[str, str] = {
    "beginner": (
        "## 难度约束（极为重要）\n"
        "该学习者为零基础初学者。请遵守以下规则：\n"
        "1. 使用最简单直白的语言，优先用日常生活类比来解释抽象概念（如用「图书馆索引」类比数据库索引）。\n"
        "2. 每条论断只聚焦一个核心概念，不要在一个论断里堆砌多个概念。\n"
        "3. 避免引入需要 Python/SQL/编程背景的概念——即使引用的条目里有代码示例，"
        "也应当先用纯自然语言描述，再附上最简单的代码。\n"
        "4. 不要使用专业术语黑话，如需引入新概念必须在论断中当场解释。"
    ),
    "intermediate": (
        "## 难度约束\n"
        "该学习者有一定基础。可使用适度专业术语，但首次出现时需简要解释。"
    ),
    "advanced": (
        "## 难度约束\n"
        "该学习者基础扎实。可使用标准专业术语和复杂场景举例，着重知识间的联系与综合应用。"
    ),
}

GENERATE_PROMPT = """你是大数据分析领域的培训讲师。根据以下材料生成一份个性化学习讲义。

学员画像摘要：{profile_summary}
教学大纲：{outline}
{difficulty_instruction}
上轮审核反馈（如有，请逐条回应：采纳并修正，或说明反驳理由）：{feedback}
{uncovered_section}
{retry_section}
{advance_section}
{dedup_section}
【本次教学主题条目】（必须围绕它讲，这是本轮唯一要教透的内容）：
{anchor_entry}

【背景条目】（仅作理解背景，不得展开讲解、不得作为讲授主体）：
{aux_entries}

要求：
1. 生成 3-5 条论断，每条是一段完整的教学内容（50-100 字）。
2. 每条论断必须标注 evidence_ids：列出支撑该论断的知识条目 id 列表，至少一条。
3. 内容严格基于条目原文，不编造不在条目中的知识点。
4. 根据画像调整难度和举例风格。
5. 严禁讲解背景条目中的知识点——主题条目里没有的内容一律不教。
6. 论断分类，按 class 标注：
   - "core"：常规论断，严格基于条目原文（默认）；
   - "extension"：仅当存在【学生错因与上轮作答】段时必须至少新增 1 条——
     直接指出学生错在哪里、正确理解是什么；允许应用级示例与推导
     （如具体的表设计、计算步骤），但必须从引用的条目概念推导而来，
     不得与条目内容相悖，也不得引入条目之外的新概念；
   - "procedure_guide"：仅当【本次教学主题条目】标注 "knowledge_type": "procedure"
     时必须产出 2-3 条——步骤化实操指南，每条一个操作步骤
     （步骤 + 可运行示例 + 检查点），学生照做即可完成上机实操。
     同样基于条目原文，evidence_ids 照常标注。非 procedure 条目禁止使用此类型。

严格按 JSON 输出：
{{"draft": [{{"claim_index": 1, "text": "...", "evidence_ids": ["..."], "claim_type": "core"}}]}}"""


async def generate_node(
    state: AgentState, *, provider: LLMProvider, model: str | None = None
) -> dict:
    retrieved = state.get("retrieved_entries", [])
    anchor = state.get("anchor_entry")
    uncovered = state.get("uncovered_gaps", [])

    if anchor is not None:
        anchor_entry_text = json.dumps(
            {
                "id": anchor.id,
                "title": anchor.title,
                "content": anchor.content,
                "knowledge_type": getattr(anchor, "knowledge_type", "concept"),
            },
            ensure_ascii=False,
            indent=2,
        )
        aux_entries = [e for e in retrieved if e.id != anchor.id]
        aux_text = json.dumps(
            [{"id": e.id, "title": e.title, "content": e.content} for e in aux_entries],
            ensure_ascii=False,
            indent=2,
        )
    else:
        anchor_entry_text = "（未指定，可从下方条目中自行选择教学主题）"
        aux_text = "（无）"

    uncovered_section = (
        "以下盲区知识库未覆盖，必须在讲义中明确标注「知识库未覆盖，建议补充学习」，严禁编造其内容："
        + json.dumps(uncovered, ensure_ascii=False)
        if uncovered
        else ""
    )

    retry_context = state.get("retry_context", "")
    retry_section = (
        "【学生错因与上轮作答】（学生刚答错，这是本轮教学的第一优先级，"
        "必须针对性地回应，不能回避）：\n" + retry_context
        if retry_context
        else ""
    )

    advance_hint = state.get("advance_hint", "")
    advance_section = (
        "【教学推进提示】（学生识别层已通过）：\n" + advance_hint
        if advance_hint
        else ""
    )

    taught_previously = state.get("taught_previously", [])
    dedup_section = (
        "【已教内容】（此前各轮已讲过——本轮禁止复读这些论断的信息点，"
        "重教必须提供增量：针对错因的应用、换角度的深化、或补充未覆盖细节）：\n"
        + "\n".join(f"- {t}" for t in taught_previously)
        if taught_previously
        else ""
    )

    output = await provider.chat_validated(
        [
            {
                "role": "user",
                "content": GENERATE_PROMPT.format(
                    profile_summary=state.get("profile_summary", ""),
                    outline=json.dumps(state.get("outline", {}), ensure_ascii=False),
                    difficulty_instruction=DIFFICULTY_INSTRUCTION.get(
                        state.get("difficulty_level", "beginner"), ""
                    ),
                    feedback=state.get("last_review_feedback", ""),
                    uncovered_section=uncovered_section,
                    retry_section=retry_section,
                    advance_section=advance_section,
                    dedup_section=dedup_section,
                    anchor_entry=anchor_entry_text,
                    aux_entries=aux_text,
                ),
            }
        ],
        schema=GenerateOutput,
        model=model,
    )

    cited_ids = {eid for claim in output.draft for eid in claim.evidence_ids}
    cited_entries = [e for e in retrieved if e.id in cited_ids]
    logger.info("generate_done", claims_count=len(output.draft), cited_count=len(cited_entries))
    return {"draft": output.draft, "cited_entries": cited_entries}
