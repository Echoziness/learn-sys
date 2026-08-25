"""审核合并逻辑：每条论断恰好一条裁决，规则层优先，漏判 fail-closed，索引越界丢弃；
裁决日志模型：论断的当前裁决 = 日志中该论断最新一条（后写覆盖先写）。"""

from core.agents.review import (
    build_feedback,
    build_rejected_claims,
    latest_verdicts,
    merge_verdicts,
    rule_check,
)
from core.state import DraftClaim, RetrievedEntry, ReviewNote


def _draft() -> list[DraftClaim]:
    return [
        DraftClaim(claim_index=1, text="论断一", evidence_ids=["E1"]),
        DraftClaim(claim_index=2, text="论断二", evidence_ids=["E404"]),
        DraftClaim(claim_index=3, text="论断三", evidence_ids=["E1"]),
    ]


def _cited() -> list[RetrievedEntry]:
    return [RetrievedEntry(id="E1", title="条目一", content="原文", score=0.9)]


def test_rule_check_flags_missing_evidence():
    notes, flagged = rule_check(_draft(), _cited())
    assert flagged == {2}
    assert notes[0].verdict == "unsupported"
    assert "E404" in notes[0].reason


def test_merge_one_verdict_per_claim():
    """规则层已裁决的论断不再送 NLI；NLI 返回它也必须被丢弃——杜绝双重计数。"""
    rule_notes, _ = rule_check(_draft(), _cited())
    nli_notes = [
        ReviewNote(claim_index=1, verdict="supported", reason="原文支持"),
        ReviewNote(claim_index=2, verdict="supported", reason="NLI 不应收到此条"),
        ReviewNote(claim_index=99, verdict="supported", reason="编造的索引"),
    ]
    merged = merge_verdicts(_draft(), rule_notes, nli_notes)
    assert len(merged) == 3
    by_index = {n.claim_index: n for n in merged}
    assert by_index[1].verdict == "supported"
    assert by_index[2].verdict == "unsupported"  # 规则层优先，不被 NLI 覆盖
    assert by_index[3].verdict == "unsupported"  # NLI 漏判 → fail-closed
    assert "fail-closed" in by_index[3].reason


def test_build_feedback_only_contains_issues():
    notes = [
        ReviewNote(claim_index=1, verdict="supported", reason="ok"),
        ReviewNote(claim_index=2, verdict="unsupported", reason="bad", suggestion="fix"),
    ]
    feedback = build_feedback(notes)
    assert "unsupported" in feedback
    assert "claim_index" in feedback
    assert build_feedback([notes[0]]) == ""


def test_build_rejected_claims_only_unsupported_with_text():
    """定向改写通道：只收 unsupported（partially 不触发改写），携带原文与理由。"""
    draft = [
        DraftClaim(claim_index=1, text="通过的论断", evidence_ids=["E1"]),
        DraftClaim(claim_index=2, text="被驳回的论断", evidence_ids=["E1"]),
        DraftClaim(claim_index=3, text="过度延伸的论断", evidence_ids=["E1"]),
    ]
    notes = [
        ReviewNote(claim_index=1, verdict="supported", reason="ok"),
        ReviewNote(claim_index=2, verdict="unsupported", reason="编造", suggestion="删除"),
        ReviewNote(claim_index=3, verdict="partially_supported", reason="常识引申"),
    ]
    rejected = build_rejected_claims(draft, notes)
    assert len(rejected) == 1
    assert rejected[0]["claim_index"] == 2
    assert rejected[0]["text"] == "被驳回的论断"
    assert rejected[0]["reason"] == "编造"
    assert rejected[0]["suggestion"] == "删除"


def test_latest_verdicts_last_write_wins():
    """裁决属于论断不属于轮：同论断取日志最新一条，其余论断各自保留。"""
    history = [
        ReviewNote(claim_index=1, verdict="supported", reason="首轮通过"),
        ReviewNote(claim_index=2, verdict="unsupported", reason="首轮驳回"),
        ReviewNote(claim_index=2, verdict="supported", reason="改写后通过"),
    ]
    latest = latest_verdicts(history)
    assert latest[1].verdict == "supported"
    assert latest[2].verdict == "supported"
    assert latest[2].reason == "改写后通过"


def test_latest_verdicts_empty():
    assert latest_verdicts([]) == {}
