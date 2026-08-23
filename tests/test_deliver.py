"""deliver 资源组装：讲义只收审核通过论断、题目归档、指南提取、进阶标记、难度层级。"""

from typing import Literal

from core.deliver import (
    archive_questions,
    build_challenge,
    build_lecture,
    difficulty_tier_for,
    extract_practice,
    is_tier_matched,
    package_to_entry,
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


# ---------- package_to_entry：资源包 → 知识库条目（同构导出） ----------


class _Entry:
    id = "BDA-SQL-001"
    title = "SELECT 基础查询"
    knowledge_type = "procedure"
    difficulty = 2
    prerequisites = ["BDA-DB-001"]
    keywords = ["SQL", "SELECT", "FROM", "查询"]
    source = "ISO/IEC 9075"


def _pkg(lecture: list[dict]) -> dict:
    return {"entry_id": "BDA-SQL-001", "lecture": lecture}


def test_package_to_entry_basic_and_inherit():
    pkg = _pkg([
        {"text": "SELECT 语句用于从数据库检索数据，配合 FROM 构成查询。", "evidence_ids": ["BDA-SQL-001"]},
        {"text": "使用 DISTINCT 去除查询结果中的重复行。", "evidence_ids": ["BDA-SQL-001"]},
    ])
    item = package_to_entry(
        pkg, _Entry, learner_id="p01", difficulty_level="beginner", claims_total=3
    )
    assert item is not None
    assert item["id"] == "GEN-BDA-SQL-001-p01"
    assert item["title"] == "SELECT 基础查询（零基础适配版）"
    assert "SELECT 语句" in item["content"] and "DISTINCT" in item["content"]
    # 继承源条目的依赖/难度/类型
    assert item["prerequisites"] == ["BDA-DB-001"]
    assert item["difficulty"] == 2
    assert item["knowledge_type"] == "procedure"
    # keywords 过滤到 content 实际命中的（"查询"/"SQL"? SQL 不在 content 字符里）
    assert "SELECT" in item["keywords"] and "查询" in item["keywords"]
    assert "FROM" in item["keywords"]
    # 溯源链改写：生成来源 + 审核通过率
    assert "生成自 BDA-SQL-001" in item["source"] and "2/3" in item["source"]


def test_package_to_entry_empty_lecture_returns_none():
    assert (
        package_to_entry(_pkg([]), _Entry, learner_id="p01", difficulty_level="beginner", claims_total=0)
        is None
    )


def test_package_to_entry_pitfalls_appended():
    pkg = _pkg([{"text": "SELECT 配合 FROM 检索表数据。", "evidence_ids": ["BDA-SQL-001"]}])
    item = package_to_entry(
        pkg, _Entry, learner_id="p01", difficulty_level="intermediate",
        claims_total=1, pitfalls=["常见误区：认为 SELECT 会修改表数据；正确理解是只读。"],
    )
    assert item is not None
    assert "常见误区" in item["content"] and "只读" in item["content"]
    assert "进阶适配版" in item["title"]


def test_package_to_entry_pitfall_prefix_dedup():
    """LLM 提炼物自带"常见误区："前缀时不得重复拼接。"""
    pkg = _pkg([{"text": "SELECT 配合 FROM 检索表数据。", "evidence_ids": ["BDA-SQL-001"]}])
    item = package_to_entry(
        pkg, _Entry, learner_id="p01", difficulty_level="beginner",
        claims_total=1,
        pitfalls=["常见误区：认为 SELECT 会修改数据；正确理解是只读。"],
    )
    assert item is not None
    assert item["content"].count("常见误区") == 1
