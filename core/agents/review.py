"""审核裁判 Agent。仅访问 draft 和 cited_entries，不接触 learner_profile。

架构强制隔离：review_node 从 state 只提取这两个字段，审核逻辑签名里没有画像字段。
每条论断保证恰好一条最终裁决（规则层优先、NLI 补充、漏判 fail-closed 记 unsupported），
使幻觉率等指标的分母恒等于论断总数，口径可复算。
"""

import json

import structlog

from core.llm import LLMProvider
from core.state import AgentState, DraftClaim, RetrievedEntry, ReviewNote, ReviewOutput

logger = structlog.get_logger()

REVIEW_PROMPT = """你是知识审核裁判。你的任务是逐条判断：给定论断是否被其引用的知识条目原文所支持。

不需要考虑学习者的背景，不需要评价论断的教学质量——只判断一个问题：
论断引用的 evidence_ids 对应条目的原文，是否真正支持该论断。

对每条论断给出裁决：
- supported：条目原文明确支持该论断，关键事实吻合
- partially_supported：条目原文部分支持，但论断有过度推断或添加了没有依据的细节
- unsupported：条目原文不支持或矛盾于该论断

被引用条目列表（id → content）：
{cited_entries_map}

待审核的论断列表：
{draft}

严格按以下 JSON 格式输出（claim_index 必须与输入一一对应）：
{{"reviews": [{{"claim_index": 1, "verdict": "supported", "reason": "判断理由", "suggestion": "若不通过，给出修改建议"}}, ...]}}
"""


def rule_check(
    draft: list[DraftClaim], cited_entries: list[RetrievedEntry]
) -> tuple[list[ReviewNote], set[int]]:
    """规则层：引用不存在的条目 → 直接裁 unsupported，不再送 NLI（避免双重计数）。"""
    valid_ids = {e.id for e in cited_entries}
    notes: list[ReviewNote] = []
    flagged: set[int] = set()
    for claim in draft:
        missing = [eid for eid in claim.evidence_ids if eid not in valid_ids]
        if missing:
            flagged.add(claim.claim_index)
            notes.append(
                ReviewNote(
                    claim_index=claim.claim_index,
                    verdict="unsupported",
                    reason=f"引用的条目 {missing} 不在已检索条目中",
                    suggestion="移除无效引用，仅引用已检索到的条目",
                )
            )
    return notes, flagged


def merge_verdicts(
    draft: list[DraftClaim],
    rule_notes: list[ReviewNote],
    nli_notes: list[ReviewNote],
) -> list[ReviewNote]:
    """合并两层裁决：规则层优先；NLI 编造/遗漏 claim_index 显式处理；每条论断恰好一条裁决。"""
    rule_by_index = {n.claim_index: n for n in rule_notes}
    expected = {c.claim_index for c in draft} - set(rule_by_index)
    merged: dict[int, ReviewNote] = dict(rule_by_index)

    for note in nli_notes:
        if note.claim_index not in expected:
            logger.warning("review_index_out_of_scope", claim_index=note.claim_index)
            continue
        merged[note.claim_index] = note

    missing = expected - set(merged)
    for idx in sorted(missing):
        logger.warning("review_missing_verdict_failsafe", claim_index=idx)
        merged[idx] = ReviewNote(
            claim_index=idx,
            verdict="unsupported",
            reason="审核未覆盖该论断，按 fail-closed 原则记为不支持",
            suggestion="重新生成该论断并确保引用条目原文",
        )
    return [merged[i] for i in sorted(merged)]


def build_feedback(notes: list[ReviewNote]) -> str:
    """生成 Agent 下一轮的唯一反馈通道：结构化打回意见（哪条论断、什么问题、建议动作）。"""
    issues = [n for n in notes if n.verdict != "supported"]
    if not issues:
        return ""
    return json.dumps([n.model_dump() for n in issues], ensure_ascii=False, indent=2)


async def review_node(
    state: AgentState, *, provider: LLMProvider, model: str | None = None
) -> dict:
    draft = state.get("draft", [])
    cited_entries = state.get("cited_entries", [])

    rule_notes, flagged = rule_check(draft, cited_entries)
    pending = [c for c in draft if c.claim_index not in flagged]

    nli_notes: list[ReviewNote] = []
    if pending:
        cited_map = json.dumps({e.id: e.content for e in cited_entries}, ensure_ascii=False)
        draft_text = json.dumps([c.model_dump() for c in pending], ensure_ascii=False)
        output = await provider.chat_validated(
            [{"role": "user", "content": REVIEW_PROMPT.format(cited_entries_map=cited_map, draft=draft_text)}],
            schema=ReviewOutput,
            model=model,
        )
        nli_notes = output.reviews

    notes = merge_verdicts(draft, rule_notes, nli_notes)
    unsupported = [n for n in notes if n.verdict == "unsupported"]
    logger.info("review_done", total=len(notes), unsupported_count=len(unsupported))

    return {
        "review_history": notes,
        "review_round": state.get("review_round", 0) + 1,
        "last_review_feedback": build_feedback(notes),
    }
