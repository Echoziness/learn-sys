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
{followup_section}
{advance_section}
{dedup_section}
【本次教学主题条目】（必须围绕它讲，这是本轮唯一要教透的内容）：
{anchor_entry}

【背景条目】（仅作理解背景，不得展开讲解、不得作为讲授主体）：
{aux_entries}

要求（教学弧——论断按教学顺序组织，回答"是什么 / 怎么用 / 注意什么"三层）：
1. 生成 4-6 条论断，每条 80-150 字，是一条完整可读的教学段落：
   - 概念论断（1-2 条，core）：这是什么——定义 + 直观类比
     （零基础学员必须有类比，进阶以上可直接用术语讲）；
   - 示例论断（1-2 条，core）：怎么用——用具体数据/代码走一遍：
     SQL 写出可执行语句并说明每部分的作用，pandas 写出调用代码和它返回什么。
     示例必须从条目概念推导而来（把条目语法具体化为真实表名列名是允许的），
     不得引入条目之外的新概念或新语法；
   - 要点论断（1-2 条，core）：把条目原文中**写明的**规则、默认行为、边界
     条件组织成"注意"的形式（如默认排序方向、运算符优先级、NULL 的处理、
     DISTINCT 对整行还是整列生效）——内容必须出自条目原文，只是换一种
     强调方式。**禁止添加条目未写的工程实践建议**（性能开销、编码规范、
     分号习惯、行业惯例、"建议谨慎使用"类评语）——条目里没有的条条框框
     写了必被审核打回。
2. 每条论断必须标注 evidence_ids：列出支撑该论断的知识条目 id 列表，至少一条。
3. 概念事实严格基于条目原文，不编造不在条目中的知识点——包括"常见误解"、
   "对比辨析"、"选型建议"、"工程实践建议"等扩展性内容：除非能从条目原文
   直接推导，否则不要写（写了会被审核打回）。示例中的具体表名/列名/数据值
   是概念的具体化，不算编造；但示例之外的结论性内容必须有条目依据。
   判断标准：把论断里的具体例子删掉，剩下的概念性陈述必须能在条目里找到。
4. 根据画像调整难度和举例风格。
5. 严禁讲解背景条目中的知识点——主题条目里没有的内容一律不教。
6. 论断分类，按 class 标注：
   - "core"：概念论断、示例论断、要点论断都是 core（默认）；
   - "extension"：仅当存在【学生错因与上轮作答】段时必须至少新增 1 条——
     直接指出学生错在哪里、正确理解是什么；允许应用级示例与推导
     （如具体的表设计、计算步骤），但必须从引用的条目概念推导而来，
     不得与条目内容相悖，也不得引入条目之外的新概念；
   - "procedure_guide"：仅当【本次教学主题条目】标注 "knowledge_type": "procedure"
     时必须产出 2-3 条——步骤化实操指南，承担"示例论断 + 要点论断"的角色
     （每条一个操作步骤：步骤 + 可运行示例 + 检查点），学生照做即可完成上机实操。
     此时概念论断压缩到 1-2 条即可。非 procedure 条目禁止使用此类型。

严格按 JSON 输出：
{{"draft": [{{"claim_index": 1, "text": "...", "evidence_ids": ["..."], "claim_type": "core"}}]}}"""

# 定向改写（2026-08-26）：只重写被驳回的论断，supported 部分原封不动——
# 整稿重写会把已通过的论断重新生成、甚至复现已被驳回的内容（实测踩坑）。
REWRITE_PROMPT = """你是大数据分析领域的培训讲师。上一轮讲义中有几条论断被审核驳回，
请只改写这些被驳回的论断，其余论断已通过审核、不需要你处理。

【本次教学主题条目】（改写必须严格基于它）：
{anchor_entry}

【被驳回的论断及驳回理由】（逐条重写，逐条回应驳回理由）：
{rejected}

改写要求：
1. 输出的每条论断的 claim_index 必须与被驳回的论断一一对应（原位替换）；
2. 删除被驳回的无依据内容，只保留条目原文支持的部分；若该论断删掉无依据
   部分后空洞，则换一个条目确有依据的角度重写（仍聚焦同一教学点）；
3. 严禁把被驳回的原话换个说法再写一遍——那会再次被驳回；
4. evidence_ids 必须来自上述主题条目；每条 80-150 字，保持教学段落形态；
   claim_type 与被驳回论断保持一致。
5. 学员画像摘要（仅供调整表达难度）：{profile_summary}

严格按 JSON 输出（只输出被驳回论断的替代版，数量与输入一致）：
{{"draft": [{{"claim_index": 2, "text": "...", "evidence_ids": ["..."], "claim_type": "core"}}]}}"""


async def generate_node(
    state: AgentState, *, provider: LLMProvider, model: str | None = None
) -> dict:
    rejected = state.get("rejected_claims", [])
    if rejected and state.get("draft"):
        return await _rewrite_rejected(state, rejected, provider=provider, model=model)
    return await _generate_full(state, provider=provider, model=model)


async def _rewrite_rejected(
    state: AgentState, rejected: list[dict], *, provider: LLMProvider, model: str | None
) -> dict:
    """定向改写：只重写被驳回论断，合并回原稿（保持 claim_index 与通过论断不变）。"""
    anchor = state.get("anchor_entry")
    anchor_entry_text = (
        json.dumps(
            {
                "id": anchor.id,
                "title": anchor.title,
                "content": anchor.content,
                "knowledge_type": getattr(anchor, "knowledge_type", "concept"),
            },
            ensure_ascii=False,
            indent=2,
        )
        if anchor is not None
        else "（未指定）"
    )
    output = await provider.chat_validated(
        [
            {
                "role": "user",
                "content": REWRITE_PROMPT.format(
                    anchor_entry=anchor_entry_text,
                    rejected=json.dumps(rejected, ensure_ascii=False, indent=2),
                    profile_summary=state.get("profile_summary", ""),
                ),
            }
        ],
        schema=GenerateOutput,
        model=model,
    )

    # 原位替换：只接受与被驳回论断对应的替代版，多余输出丢弃（防改写面扩散）
    rejected_indices = {int(r["claim_index"]) for r in rejected}
    replacements = {
        c.claim_index: c for c in output.draft if c.claim_index in rejected_indices
    }
    merged_draft = [replacements.get(c.claim_index, c) for c in state.get("draft", [])]
    # 改写失败兜底（LLM 漏写某条）：保留原论断——裁决会再次记不支持，不静默吞错因。
    for idx in rejected_indices - set(replacements):
        logger.warning("rewrite_missing_replacement", claim_index=idx)
    cited_ids = {eid for claim in merged_draft for eid in claim.evidence_ids}
    cited_entries = [e for e in state.get("retrieved_entries", []) if e.id in cited_ids]
    logger.info(
        "generate_rewrite_done",
        rewritten=len(replacements),
        rejected=len(rejected_indices),
        claims_count=len(merged_draft),
    )
    return {"draft": merged_draft, "cited_entries": cited_entries}


async def _generate_full(
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

    # 困惑回流（2026-08-28）：学生主动提问的困惑是比错因更直接的教学锚点，
    # 本轮必须针对性讲解（当时已给过简答，此处要讲透）；仍是 core 论断（条目范围内）
    followup_context = state.get("followup_context", "")
    followup_section = (
        "【学生主动提出的疑问】（上次教学后记录、尚未被教学消化的困惑，"
        "本轮必须针对性讲解到位——每条疑问都要有论断正面回应，不得回避）：\n"
        + followup_context
        if followup_context
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
                    followup_section=followup_section,
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
