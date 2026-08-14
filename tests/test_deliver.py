"""deliver 资源组装：讲义只收审核通过论断、题目归档、指南提取、进阶标记、难度层级。"""

from typing import Literal

from core.deliver import (
    archive_questions,
    build_challenge,
    build_lecture,
    difficulty_tier_for,
    extract_practice,
    is_tier_matched,
)
from core.state import DraftClaim, ReviewNote

ClaimType = Literal["core", "extension", "procedure_guide"]
Verdict = Literal["supported", "partially_supported", "unsupported"]


def _claim(idx: int, ctype: ClaimType = "core", text: str = "论断") -> DraftClaim:
    return DraftClaim(claim_index=idx, text=f"{text}{idx}", evidence_ids=["E1"], claim_type=ctype)


def _note(idx: int, verdict: Verdict) -> ReviewNote:
    return ReviewNote(claim_index=idx, verdict=verdict, reason="测试")


def test_lecture_only_supported_claims():
    claims = [_claim(1), _claim(2), _claim(3)]
    reviews = [_note(1, "supported"), _note(2, "unsupported"), _note(3, "partially_supported")]
    lecture = build_lecture(claims, reviews, round_no=2)
    # 幻觉防控的资源侧收口：只有 supported 进讲义
    assert [c["text"] for c in lecture] == ["论断1"]
    assert lecture[0]["round"] == 2
    assert lecture[0]["evidence_ids"] == ["E1"]


def test_lecture_missing_verdict_fails_closed():
    """无裁决的论断不进讲义（fail-closed）。"""
    lecture = build_lecture([_claim(1)], [], round_no=1)
    assert lecture == []


def test_archive_questions_from_rounds():
    rounds = [
        {
            "entry_id": "E1",
            "round_no": 1,
            "question": {
                "question_id": "q_E1_r1", "entry_id": "E1", "question_type": "choice",
                "prompt": "选择题", "options": ["A. x", "B. y"], "expected_label": "A",
            },
            "answer": "A",
        },
        {
            "entry_id": "E1",
            "round_no": 2,
            "question": {
                "question_id": "q_E1_r2_scaffold", "entry_id": "E1", "question_type": "choice",
                "prompt": "脚手架", "options": ["A. x"], "expected_label": "A",
            },
            "answer": None,  # 未作答的 pending 轮不归档
        },
    ]
    archived = archive_questions(rounds)
    assert len(archived) == 1
    assert archived[0]["round"] == 1
    assert "expected" not in str(archived)  # 判分要点不进学生可见结构


def test_practice_only_for_procedure_entry():
    claims = [_claim(1, "procedure_guide", "步骤"), _claim(2, "core", "概念")]
    reviews = [_note(1, "supported"), _note(2, "supported")]
    # 非 procedure 条目：即使有 guide 论断也不产出
    assert extract_practice(claims, reviews, knowledge_type="concept") is None
    practice = extract_practice(claims, reviews, knowledge_type="procedure")
    assert practice is not None
    assert [s["text"] for s in practice["steps"]] == ["步骤1"]
    # 未获支持的步骤被剔除
    reviews_bad = [_note(1, "unsupported"), _note(2, "supported")]
    assert extract_practice(claims, reviews_bad, knowledge_type="procedure") is None


def test_checkpoint_first_sentence():
    from core.deliver import _first_sentence

    assert _first_sentence("打开终端执行命令。然后查看输出结果。") == "打开终端执行命令。"
    assert _first_sentence("无句读符的很长很长很长很长很长很长很长很长文本") .endswith(("文本",))


def test_challenge_gate():
    assert build_challenge("主题", mastery=0.84) is None
    ch = build_challenge("主题", mastery=0.9)
    assert ch is not None and "主题" in ch["task"]


def test_difficulty_tier():
    assert difficulty_tier_for("beginner", 2) == "beginner"
    assert difficulty_tier_for("beginner", 3) == "capped:beginner"
    assert is_tier_matched("beginner") and not is_tier_matched("capped:beginner")
    assert difficulty_tier_for("intermediate", 3) == "intermediate"
    assert difficulty_tier_for("advanced", 5) == "advanced"
