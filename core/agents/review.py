"""审核裁判 Agent。仅访问 draft 和 cited_entries，不接触 learner_profile。

架构强制隔离：review_node 从 state 只提取职责内字段，审核逻辑签名里没有画像字段。
review_history 是 append-only 裁决日志：每轮只 append 本轮新裁决的论断，
论断的当前裁决 = 日志中该论断最新一条（latest_verdicts）——rewrite 轮只复审
被改写的论断，未改写论断裁决天然不变（LLM 裁决轮间非确定，重审会翻判）。
每条论断恰好一条当前裁决（规则层优先、NLI 补充、漏判 fail-closed 记 unsupported），
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

幻觉的定义（判裁基准）：幻觉 = 专业知识谬误——与条目原文矛盾、编造条目之外
的概念或不可核实的事实。条目未写明但事实正确、与条目概念一致的领域公认常识，
不是谬误，只是超出条目覆盖（判 partially_supported）。

论断分三类，审核标准不同：
- "core" 论断（条目覆盖层）：概念性陈述必须被条目原文明确支持，关键事实
  吻合——过度推断、编造事实判不通过。
  **例外一——示例句**：core 论断中的具体示例（把条目语法具体化为真实
  表名/列名/数据值的代码实例或数据实例）按"概念一致 + 推导自洽"处理：
  只要示例运用的概念、语法、行为与条目一致，换表名列名的具体化不算编造；
  但示例中出现的概念或语法超出条目范围即不通过。
  **例外二——常识引申**：条目未写明但与条目概念一致、属于领域公认常识的
  补充（如工具的通行默认行为），不算幻觉，判 partially_supported。
- "extension" 论断（错因扩展层，重教轮针对学生错因的应用级讲解）：
  不要求逐字符出自条目原文，但必须同时满足：
  ① 概念一致性——讲解内容不得与所引条目内容相悖或引入条目之外的新概念；
  ② 推导自洽——是针对学生错因的合理纠正，逻辑成立、可自圆其说。
  两条缺一即不通过。
- "procedure_guide" 论断（实操指南步骤，含可运行示例）：标准同 extension
  （概念一致 + 推导自洽）——步骤必须能从条目原文推导出操作路径；
  示例中的具体语法、工具操作细节（如"打开数据库工具输入 SQL"）允许
  自洽合理即可，不要求逐字符出自条目，但不得引入条目之外的新工具或新概念。
- 重写轮纪律：若论断与上轮被驳回内容实质相同（同一无依据细节换皮复现），
  照旧驳回。

对每条论断给出裁决：
- supported：符合上述对应标准。
- partially_supported：与条目不矛盾、概念一致，但包含条目未写明的内容——
  属于领域公认常识或合理教学引申，无法从条目原文直接核实。
- unsupported（计入幻觉率，从严）：与条目原文矛盾；或引入条目之外的新概念；
  或包含无法核实、疑似编造的具体事实。拿不准是否属实、是否与条目一致时，
  判 unsupported。

被引用条目列表（id → content）：
{cited_entries_map}

待审核的论断列表：
{draft}

严格按以下 JSON 格式输出（claim_index 必须与输入一一对应）：
{{"reviews": [{{"claim_index": 1, "verdict": "supported", "reason": "判断理由",
  "suggestion": "若不通过，给出修改建议"}}, ...]}}
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


def latest_verdicts(history: list[ReviewNote]) -> dict[int, ReviewNote]:
    """论断的当前裁决 = 裁决日志中该论断最新一条（后写覆盖先写）。

    第一性模型：裁决属于论断不属于轮——轮次只是重试上限计数器（review_round）。
    rewrite 轮只 append 被复审论断的新裁决，未改写论断的裁决天然不漂移，
    无需任何继承/拼接逻辑。
    """
    latest: dict[int, ReviewNote] = {}
    for note in history:
        latest[note.claim_index] = note
    return latest


def merge_verdicts(
    draft: list[DraftClaim],
    rule_notes: list[ReviewNote],
    nli_notes: list[ReviewNote],
) -> list[ReviewNote]:
    """合并两层裁决：规则层优先；NLI 编造/遗漏显式处理；每条论断恰好一条裁决。"""
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


def build_rejected_claims(draft: list[DraftClaim], notes: list[ReviewNote]) -> list[dict]:
    """定向改写通道：被驳回论断清单（原文 + 裁决理由），供 generate 只重写这几条。"""
    text_by_index = {c.claim_index: c.text for c in draft}
    return [
        {
            "claim_index": n.claim_index,
            "text": text_by_index.get(n.claim_index, ""),
            "verdict": n.verdict,
            "reason": n.reason,
            "suggestion": n.suggestion or "",
        }
        for n in notes
        if n.verdict == "unsupported"
    ]


async def review_node(
    state: AgentState, *, provider: LLMProvider, model: str | None = None
) -> dict:
    draft = state.get("draft", [])
    cited_entries = state.get("cited_entries", [])

    rule_notes, flagged = rule_check(draft, cited_entries)
    pending = [c for c in draft if c.claim_index not in flagged]

    # 定向打回轮：只复审被改写的论断——未改写论断的裁决已在日志里，
    # 当前裁决按论断取最新一条（防整稿复审翻判，也省 LLM 成本）
    rejected_indices = {int(r["claim_index"]) for r in state.get("rejected_claims", [])}
    if rejected_indices and state.get("review_round", 0) > 0:
        pending = [c for c in pending if c.claim_index in rejected_indices]

    nli_notes: list[ReviewNote] = []
    if pending:
        cited_map = json.dumps({e.id: e.content for e in cited_entries}, ensure_ascii=False)
        draft_text = json.dumps([c.model_dump() for c in pending], ensure_ascii=False)
        output = await provider.chat_validated(
            [
                {
                    "role": "user",
                    "content": REVIEW_PROMPT.format(
                        cited_entries_map=cited_map, draft=draft_text
                    ),
                }
            ],
            schema=ReviewOutput,
            model=model,
        )
        nli_notes = output.reviews

    notes = merge_verdicts(pending, rule_notes, nli_notes)
    unsupported = [n for n in notes if n.verdict == "unsupported"]
    logger.info("review_done", total=len(notes), unsupported_count=len(unsupported))

    # 反馈/驳回清单基于全稿当前裁决（日志 + 本轮新裁决）
    current = latest_verdicts([*state.get("review_history", []), *notes])
    all_notes = [current[c.claim_index] for c in draft if c.claim_index in current]

    return {
        "review_history": notes,
        "review_round": state.get("review_round", 0) + 1,
        "last_review_feedback": build_feedback(all_notes),
        "rejected_claims": build_rejected_claims(draft, all_notes),
    }
