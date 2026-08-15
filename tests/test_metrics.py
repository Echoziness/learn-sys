"""指标口径：幻觉率 / 画像-资源适配率 / 知识点覆盖率（PRD §5 三指标 SSOT）。"""

from core.state import DraftClaim, ReviewNote
from evals.metrics import (
    claim_verdicts,
    hallucination_rate,
    keyword_coverage,
    tier_match_rate,
)


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


# ── 画像-资源适配率 ───────────────────────────────────────────────────


def test_tier_match_rate_counts_uncapped():
    packages = [
        {"entry_id": "E1", "difficulty_tier": "beginner"},
        {"entry_id": "E2", "difficulty_tier": "capped:beginner"},
        {"entry_id": "E3", "difficulty_tier": "beginner"},
    ]
    rate, matched, total = tier_match_rate(packages)
    assert (rate, matched, total) == (2 / 3, 2, 3)


def test_tier_match_rate_empty_packages():
    assert tier_match_rate([]) == (0.0, 0, 0)


def test_tier_match_rate_intermediate_level():
    """intermediate 资源层级为 intermediate 本身（非 capped）即匹配。"""
    packages = [
        {"entry_id": "E1", "difficulty_tier": "intermediate"},
        {"entry_id": "E2", "difficulty_tier": "capped:intermediate"},
    ]
    rate, matched, total = tier_match_rate(packages)
    assert (rate, matched, total) == (0.5, 1, 2)


# ── 知识点覆盖率 ──────────────────────────────────────────────────────


def test_keyword_coverage_basic():
    packages = [
        {
            "entry_id": "E1",
            "lecture": [
                {"text": "关系型数据库以表为基本存储单元，主键唯一标识一行。"},
                {"text": "外键建立表之间的引用关系。"},
            ],
        }
    ]
    keywords = {"E1": ["表", "主键", "外键", "事务"]}
    rate, hit, total = keyword_coverage(packages, keywords)
    assert (rate, hit, total) == (3 / 4, 3, 4)


def test_keyword_coverage_skips_entries_without_package():
    """无资源包的条目不计入分母（覆盖率衡量已产资源的质量，不是学习进度）。"""
    packages = [{"entry_id": "E1", "lecture": [{"text": "覆盖甲和乙"}]}]
    keywords = {"E1": ["甲", "乙"], "E2": ["丙", "丁"]}
    rate, hit, total = keyword_coverage(packages, keywords)
    assert (rate, hit, total) == (1.0, 2, 2)


def test_keyword_coverage_empty_lecture_counts_misses():
    """有资源包但讲义为空：keywords 全部未命中（分母计入，防空包美化指标）。"""
    packages = [{"entry_id": "E1", "lecture": []}]
    rate, hit, total = keyword_coverage(packages, {"E1": ["甲"]})
    assert (rate, hit, total) == (0.0, 0, 1)


def test_keyword_coverage_case_insensitive_latin():
    packages = [{"entry_id": "E1", "lecture": [{"text": "SELECT 与 FROM 是 SQL 子句"}]}]
    rate, hit, total = keyword_coverage(packages, {"E1": ["select", "SQL"]})
    assert (rate, hit, total) == (1.0, 2, 2)
