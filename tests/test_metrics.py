"""指标口径：幻觉率 = 无溯源支持论断 / 总论断，分母恒为论断数而非裁决数。"""

from core.state import DraftClaim, ReviewNote
from evals.metrics import claim_verdicts, hallucination_rate


def _draft(n: int) -> list[DraftClaim]:
    return [DraftClaim(claim_index=i, text=f"论断{i}", evidence_ids=["E1"]) for i in range(1, n + 1)]


def test_hallucination_rate_basic():
    reviews = [
        ReviewNote(claim_index=1, verdict="supported", reason="ok"),
        ReviewNote(claim_index=2, verdict="unsupported", reason="bad"),
        ReviewNote(claim_index=3, verdict="partially_supported", reason="half"),
    ]
    assert hallucination_rate(_draft(3), reviews) == 1 / 3


def test_unreviewed_claim_counts_as_unsupported():
    """fail-closed：审核漏判的论断计入幻觉，保证指标不被人为美化。"""
    reviews = [ReviewNote(claim_index=1, verdict="supported", reason="ok")]
    assert hallucination_rate(_draft(2), reviews) == 0.5


def test_duplicate_verdicts_fail_closed():
    """同一论断多条裁决时 unsupported 优先，且分母仍是论断数。"""
    reviews = [
        ReviewNote(claim_index=1, verdict="supported", reason="NLI"),
        ReviewNote(claim_index=1, verdict="unsupported", reason="规则层"),
    ]
    assert claim_verdicts(_draft(1), reviews)[1] == "unsupported"
    assert hallucination_rate(_draft(1), reviews) == 1.0


def test_empty_draft_is_zero():
    assert hallucination_rate([], []) == 0.0
