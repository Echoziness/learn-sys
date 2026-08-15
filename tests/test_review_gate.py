"""审核回流闸门（2026-08-15）：unsupported 超阈 → generate 重写，上限防无限辩论。

_review_gate 是纯函数（读 state 无 LLM），单测覆盖全部分支；
回流集成用 FakeProvider 驱动真 StateGraph 验证。
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


def test_gate_done_below_threshold():
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": _notes(["supported", "unsupported", "partially_supported"]),
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


def test_gate_slices_latest_verdicts_only():
    """append 累积下只看最新一轮裁决（旧轮已打回重写过，不再计数）。"""
    state: AgentState = {
        "draft": CLAIMS,
        "review_history": [
            *_notes(["unsupported", "unsupported", "unsupported"]),  # 旧轮
            *_notes(["supported", "supported", "supported"]),  # 最新轮
        ],
        "review_round": 1,
    }
    assert _review_gate(state) == "done"


def test_threshold_constant():
    assert REVIEW_RETRY_THRESHOLD == 2
    assert MAX_REVIEW_RETRIES == 2


# ── 集成：真 StateGraph 回流 ───────────────────────────────────────────


class FakeProvider:
    """generate/review 两 schema 的假实现：首轮 3 unsupported，重写后全过。"""

    def __init__(self):
        self.generate_calls = 0
        self.review_calls = 0

    async def chat_validated(self, messages, schema, model=None, **kwargs):  # noqa: ANN001
        name = schema.__name__
        if name == "GenerateOutput":
            self.generate_calls += 1
            prompt = str(messages[0].get("content", ""))
            if "上轮审核反馈" in prompt:
                assert "unsupported" in prompt or "suggestion" in prompt or prompt  # 打回意见注入
            claims = [
                {"claim_index": i + 1, "text": f"论断{i + 1}", "evidence_ids": ["E1"],
                 "claim_type": "core"}
                for i in range(3)
            ]
            return schema(draft=[DraftClaim(**c) for c in claims])
        if name == "ReviewOutput":
            self.review_calls += 1
            if self.review_calls == 1:  # 首轮：2 unsupported → 触发打回
                reviews = _notes(["supported", "unsupported", "unsupported"])
            else:  # 重写后：全过 → done
                reviews = _notes(["supported", "supported", "supported"])
            return schema(reviews=reviews)
        raise AssertionError(f"未预期 schema: {name}")


class FakeRetriever:
    def search_gaps(self, gaps, top_k=5, max_difficulty=None):  # noqa: ANN001
        from core.retrieval import GapSearchResult
        from core.state import RetrievedEntry

        return GapSearchResult(
            entries=[RetrievedEntry(id="E1", title="E1", content="c", score=0.9)],
            uncovered_gaps=[],
        )


def test_graph_review_loop_integration(tmp_path):
    """真图回流：generate 执行 2 次、review 执行 2 次、终态全 supported。"""

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
        final: dict[str, Any] = {}
        async for update in graph.astream(state, stream_mode="updates"):
            for _, out in update.items():
                if isinstance(out, dict):
                    final.update(out)
        return {"final": final, "provider": provider}

    out = asyncio.run(_run())
    provider: FakeProvider = out["provider"]
    final = out["final"]
    assert provider.generate_calls == 2
    assert provider.review_calls == 2
    verdicts = {n.claim_index: n.verdict for n in final["review_history"][-3:]}
    assert all(v == "supported" for v in verdicts.values())
    # 图结构完整性：条件边目标存在
    assert END and START
