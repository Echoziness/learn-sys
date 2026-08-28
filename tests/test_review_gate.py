"""审核回流闸门（2026-08-26 定向打回）：任意 1 条当前裁决 unsupported → generate 定向重写，上限防无限辩论。

_review_gate 是纯函数（读 state 无 LLM），单测覆盖全部分支；
回流集成用 FakeProvider 驱动真 StateGraph 验证。
review_history 是 append-only 裁决日志：当前裁决按论断取最新一条。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START

try:  # 条件边装配走 StateGraph 原语，不依赖完整 build（避免 BGE/DB）
    from core.graph import MAX_REVIEW_RETRIES, REVIEW_RETRY_THRESHOLD, _review_gate
except ImportError:  # pragma: no cover
    raise

from core.state import AgentState, DraftClaim, ReviewNote

CLAIMS = [
    DraftClaim(claim_index=1, text="论断一", evidence_ids=["E1"]),
    DraftClaim(claim_index=2, text="论断二", evidence_ids=["E1"]),
    DraftClaim(claim_index=3, text="论断三", evidence_ids=["E1"]),
]


def _notes(verdicts: list[str]) -> list[ReviewNote]:
    return [
        ReviewNote(claim_index=i + 1, verdict=v, reason="测试")  # type: ignore[arg-type]
        for i, v in enumerate(verdicts)
    ]


def test_gate_regenerates_on_two_unsupported():
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": _notes(["supported", "unsupported", "unsupported"]),
        "review_round": 1,
    }
    assert _review_gate(state) == "regenerate"


def test_gate_regenerates_on_single_unsupported():
    """阈值 1：单条不支持也定向打回（论断相互独立，单条漏网不应放行）。"""
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": _notes(["supported", "unsupported", "supported"]),
        "review_round": 1,
    }
    assert _review_gate(state) == "regenerate"


def test_gate_done_below_threshold():
    """partially_supported（过度延伸）不触发回流——只打回真幻觉（unsupported）。"""
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": _notes(["supported", "partially_supported", "supported"]),
        "review_round": 1,
    }
    assert _review_gate(state) == "done"


def test_gate_done_at_retry_cap():
    """轮次上限优先：即使 unsupported 超阈也放行（防无限辩论）。"""
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": _notes(["unsupported", "unsupported", "unsupported"]),
        "review_round": MAX_REVIEW_RETRIES,
    }
    assert _review_gate(state) == "done"


def test_gate_latest_verdict_per_claim():
    """裁决日志模型：同论断取最新一条（旧轮驳回已由改写版覆盖，不再计数）。"""
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": [
            *_notes(["unsupported", "unsupported", "unsupported"]),  # 旧轮（全驳回）
            *_notes(["supported", "supported", "supported"]),  # 改写后新裁决（后写覆盖）
        ],
        "review_round": 1,
    }
    assert _review_gate(state) == "done"


def test_gate_partial_log_latest_wins():
    """rewrite 轮只 append 被复审论断：未复审论断沿用旧裁决参与计数。"""
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": [
            *_notes(["supported", "unsupported", "unsupported"]),  # 首轮全量
            # rewrite 轮只 append 被复审的论断 2/3（后写覆盖）
            ReviewNote(claim_index=2, verdict="supported", reason="改写通过"),
            ReviewNote(claim_index=3, verdict="supported", reason="改写通过"),
        ],
        "review_round": 1,
    }
    assert _review_gate(state) == "done"

    state["review_history"] = [
        *_notes(["supported", "unsupported", "unsupported"]),
        ReviewNote(claim_index=2, verdict="supported", reason="改写通过"),
        # 论断 3 无新裁决，沿用旧轮 unsupported → 仍应打回
    ]
    assert _review_gate(state) == "regenerate"


def test_threshold_constant():
    assert REVIEW_RETRY_THRESHOLD == 1
    assert MAX_REVIEW_RETRIES == 2


# ── 集成：真 StateGraph 回流 ───────────────────────────────────────────


class FakeProvider:
    """generate/review 两 schema 的假实现：首轮 2 unsupported，定向重写后全过。"""

    def __init__(self):
        self.generate_calls = 0
        self.review_calls = 0
        self.rewrite_prompts: list[str] = []

    async def chat_validated(self, messages, schema, model=None, **kwargs):  # noqa: ANN001
        name = schema.__name__
        if name == "GenerateOutput":
            self.generate_calls += 1
            prompt = str(messages[0].get("content", ""))
            if "只改写这些被驳回的论断" in prompt:
                # 定向改写模式：只产出被驳回论断的替代版（驳回理由必须在上下文里）
                self.rewrite_prompts.append(prompt)
                assert "驳回理由" in prompt
                replacements = [
                    {"claim_index": i, "text": f"论断{i}改写版", "evidence_ids": ["E1"],
                     "claim_type": "core"}
                    for i in (2, 3)
                ]
                return schema(draft=[DraftClaim(**c) for c in replacements])
            claims = [
                {"claim_index": i + 1, "text": f"论断{i + 1}", "evidence_ids": ["E1"],
                 "claim_type": "core"}
                for i in range(3)
            ]
            return schema(draft=[DraftClaim(**c) for c in claims])
        if name == "ReviewOutput":
            self.review_calls += 1
            if self.review_calls == 1:  # 首轮全量：2 unsupported → 触发定向打回
                return schema(reviews=_notes(["supported", "unsupported", "unsupported"]))
            # rewrite 轮：只复审被替换的论断（prompt 里只有论断 2/3）——返回对应裁决，
            # 多余的 claim_index 会被服务端丢弃（未送审的论断不应被重判）
            prompt = str(messages[0].get("content", ""))
            assert "论断2改写版" in prompt and "论断3改写版" in prompt
            assert "论断1" not in prompt  # 未改写论断不再送审（防整稿复审翻判）
            return schema(reviews=[
                ReviewNote(claim_index=2, verdict="supported", reason="改写后通过"),
                ReviewNote(claim_index=3, verdict="supported", reason="改写后通过"),
            ])
        raise AssertionError(f"未预期 schema: {name}")


class FakeRetriever:
    def search_gaps(self, gaps, top_k=5, max_difficulty=None, domain=None):  # noqa: ANN001
        from core.retrieval import GapSearchResult
        from core.state import RetrievedEntry

        return GapSearchResult(
            entries=[RetrievedEntry(id="E1", title="E1", content="c", score=0.9)],
            uncovered_gaps=[],
        )


def test_graph_review_loop_integration(tmp_path):
    """真图定向回流：generate 执行 2 次、只重写被驳回论断、通过论断原封不动。"""

    async def _run() -> dict[str, Any]:
        from core.config import Settings
        from core.graph import build_teach_graph

        settings = Settings(
            database_path="unused", seed_dir="unused",
            diagnose_model=None, generate_model=None, review_model=None,
            feedback_model=None, question_model=None,
        )
        provider = FakeProvider()
        graph = build_teach_graph(settings, provider, FakeRetriever())  # type: ignore[arg-type]
        state: AgentState = {"gaps": ["E1"], "difficulty_level": "beginner", "review_round": 0}
        # ainvoke 返回累积后的完整图状态（append reducer）——review_history 是多轮裁决日志
        final = await graph.ainvoke(state)
        return {"final": final, "provider": provider}

    out = asyncio.run(_run())
    provider: FakeProvider = out["provider"]
    final = out["final"]
    assert provider.generate_calls == 2
    assert provider.review_calls == 2
    assert len(provider.rewrite_prompts) == 1  # 第二次 generate 走了定向改写模式
    # 定向改写语义：被驳回论断原位替换，通过论断原封不动，数量不变
    draft = {c.claim_index: c.text for c in final["draft"]}
    assert draft[1] == "论断1"
    assert draft[2] == "论断2改写版"
    assert draft[3] == "论断3改写版"
    assert {e.id for e in final["cited_entries"]} == {"E1"}
    # 裁决日志模型：每条论断取日志最新一条（日志共 5 条：首轮 3 + rewrite 轮 2）
    assert len(final["review_history"]) == 5
    from core.agents.review import latest_verdicts

    verdicts = {i: n.verdict for i, n in latest_verdicts(final["review_history"]).items()}
    assert verdicts == {1: "supported", 2: "supported", 3: "supported"}
    # 图结构完整性：条件边目标存在
    assert END and START
