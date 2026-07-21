"""审核裁判 Agent。仅访问 draft 和 cited_entries，不接触 learner_profile。

架构强制隔离：review_node 是 graph 入口（接收完整 state 但只提取指定字段），
_review_inner 是实际审核逻辑（函数签名里根本没有画像字段，编译器也拿不到）。"""

import json
import structlog
from core.llm import provider, resolve_model

logger = structlog.get_logger()

REVIEW_PROMPT = """你是知识审核裁判。你的任务是逐条判断：给定论断是否被其引用的知识条目原文所支持。

不需要考虑学习者的背景，不需要评价论断的教学质量——只判断两个问题：
1. 论断引用的 evidence_ids 是否存在于被引用条目列表中。
2. 如果存在，条目的原文是否真正支持该论断。

对每条论断给出裁决：
- supported：条目原文明确支持该论断，关键事实吻合
- partially_supported：条目原文部分支持，但论断有过度推断或添加了没有依据的细节
- unsupported：引用条目不存在，或条目原文不支持/矛盾于该论断

被引用条目列表（id → content）：
{cited_entries_map}

待审核的论断列表：
{draft}

严格按以下 JSON 格式输出：
{{"reviews": [{{"claim_index": 1, "verdict": "supported", "reason": "判断理由"}}, ...]}}
"""


async def _review_inner(draft: list[dict], cited_entries: list[dict], current_round: int) -> dict:
    """实际审核逻辑——函数签名中没有 learner_profile 等字段，架构强制隔离。"""
    valid_ids = {e["id"]: e["content"] for e in cited_entries}
    rule_issues = []
    for claim in draft:
        for eid in claim.get("evidence_ids", []):
            if eid not in valid_ids:
                rule_issues.append({
                    "claim_index": claim.get("claim_index"),
                    "verdict": "unsupported",
                    "reason": f"引用的条目 {eid} 不在已检索条目中"
                })

    cited_map = json.dumps({e["id"]: e["content"][:500] for e in cited_entries}, ensure_ascii=False)
    draft_text = json.dumps(draft, ensure_ascii=False)
    messages = [{"role": "user", "content": REVIEW_PROMPT.format(
        cited_entries_map=cited_map, draft=draft_text
    )}]

    raw = await provider.chat_json(messages, model=resolve_model("REVIEW_MODEL"))
    result = json.loads(raw)
    nli_reviews = result.get("reviews", [])
    reviews = rule_issues + nli_reviews
    unsupported = [r for r in reviews if r["verdict"] == "unsupported"]

    logger.info("review_done", total=len(reviews), unsupported_count=len(unsupported))
    return {"review_history": reviews, "review_round": current_round + 1}


async def review_node(state: dict) -> dict:
    """Graph 节点入口：只从 state 提取 draft 和 cited_entries，其余字段不传入审核逻辑。"""
    return await _review_inner(
        draft=state.get("draft", []),
        cited_entries=state.get("cited_entries", []),
        current_round=state.get("review_round", 0),
    )
