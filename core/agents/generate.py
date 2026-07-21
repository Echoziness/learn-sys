"""知识生成 Agent。仅接收检索条目 + 画像摘要 + 大纲 + 上轮反馈——其余 state 不可访问。"""

import json, structlog
from core.llm import provider, resolve_model

logger = structlog.get_logger()

GENERATE_PROMPT = """你是大数据分析领域的培训讲师。根据以下材料生成一份个性化学习讲义。

学员画像摘要：{profile_summary}
教学大纲：{outline}
上轮审核反馈（如有）：{feedback}

可引用的知识条目（每条有 id、title、content）：
{entries}

要求：
1. 生成 3-5 条论断，每条是一段完整的教学内容（50-100 字）。
2. 每条论断必须标注 evidence_ids：列出支撑该论断的知识条目 id 列表。
3. 内容严格基于条目原文，不编造不在条目中的知识点。
4. 根据画像调整难度和举例风格。

严格按 JSON 输出：{{"draft": [{{"claim_index": 1, "text": "...", "evidence_ids": ["..."]}}]}}"""


async def _generate_inner(retrieved_entries: list[dict], profile_summary: str,
                          outline: dict, last_review_feedback: str) -> dict:
    entries_text = json.dumps([
        {"id": e["id"], "title": e["title"], "content": e["content"]}
        for e in retrieved_entries
    ], ensure_ascii=False, indent=2)

    raw = await provider.chat_json([{"role": "user", "content": GENERATE_PROMPT.format(
        profile_summary=profile_summary,
        outline=json.dumps(outline, ensure_ascii=False),
        feedback=last_review_feedback,
        entries=entries_text,
    )}], model=resolve_model("GENERATE_MODEL"))
    result = json.loads(raw)
    draft = result.get("draft", [])

    cited_ids = {eid for claim in draft for eid in claim.get("evidence_ids", [])}
    cited_entries = [e for e in retrieved_entries if e["id"] in cited_ids]

    for claim in draft:
        if not claim.get("evidence_ids"):
            logger.warning("generate_missing_evidence", claim_index=claim.get("claim_index"))

    logger.info("generate_done", claims_count=len(draft), cited_count=len(cited_entries))
    return {"draft": draft, "cited_entries": cited_entries}


async def generate_node(state: dict) -> dict:
    return await _generate_inner(
        retrieved_entries=state.get("retrieved_entries", []),
        profile_summary=state.get("profile_summary", ""),
        outline=state.get("outline", {}),
        last_review_feedback=state.get("last_review_feedback", ""),
    )
