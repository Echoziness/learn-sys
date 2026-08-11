"""assess 确定性出题与判分：掌握度驱动题型（choice/answer）、关键词覆盖率、fail-closed。"""

from core.assess import (
    CHOICE_MASTERY_THRESHOLD,
    build_feedback_message,
    build_question,
    grade_answer,
)
from core.plan import KnowledgeEntry


def _entry(eid="E1", title="关系型数据库", keywords=("数据库", "表", "主键")):
    return KnowledgeEntry(
        id=eid,
        title=title,
        content="内容",
        keywords=list(keywords),
    )


# ── 题型分发（掌握度驱动）──────────────────────────────────────────────

def test_low_mastery_builds_choice():
    q = build_question(_entry(), mastery=0.0)
    assert q.question_type == "choice"
    assert q.expected_label == "A"


def test_below_threshold_builds_choice():
    q = build_question(_entry(), mastery=CHOICE_MASTERY_THRESHOLD - 0.01)
    assert q.question_type == "choice"


def test_high_mastery_builds_answer():
    q = build_question(_entry(), mastery=CHOICE_MASTERY_THRESHOLD)
    assert q.question_type == "answer"
    assert "关系型数据库" in q.prompt
    assert q.expected_keywords == ("数据库", "表", "主键")
    assert q.options == ()


def test_choice_has_correct_and_distractors():
    others = [_entry(eid="D1", title="其他A", keywords=("甲", "乙")),
              _entry(eid="D2", title="其他B", keywords=("丙", "丁")),
              _entry(eid="D3", title="其他C", keywords=("戊", "己"))]
    q = build_question(_entry(), distractors=others, mastery=0.0)
    assert len(q.options) == 4  # 1 正确 + 3 干扰
    assert "数据库、表、主键" in q.options[0]
    assert "甲、乙" in q.options[1]
    assert q.expected_keywords == ()


def test_choice_deduplicates_identical_distractor():
    others = [_entry(eid="D1", title="同", keywords=("数据库", "表", "主键")),
              _entry(eid="D2", title="异", keywords=("甲", "乙"))]
    q = build_question(_entry(), distractors=others, mastery=0.0)
    texts = [o[3:] for o in q.options]
    assert len(texts) == len(set(texts))  # 正确项与干扰项不重复
    assert len(q.options) == 2  # 干扰不足 3 个时允许少选项


# ── 判分：answer（关键词覆盖）──────────────────────────────────────────

def test_answer_grade_correct_when_all_keywords_present():
    q = build_question(_entry(), mastery=0.6)
    g = grade_answer(q, "数据库以表为存储单元，主键唯一标识一行")
    assert g.is_correct
    assert g.keyword_coverage == 1.0
    assert g.missing == ()


def test_answer_grade_partial_under_threshold():
    q = build_question(_entry(), mastery=0.6)
    g = grade_answer(q, "数据库就是把数据存起来")
    assert not g.is_correct
    assert g.missing == ("表", "主键")


def test_grade_fail_closed_empty_answer():
    q = build_question(_entry(), mastery=0.6)
    g = grade_answer(q, "")
    assert not g.is_correct
    assert g.keyword_coverage == 0.0


def test_grade_fail_closed_no_keywords():
    q = build_question(_entry(keywords=()), mastery=0.6)
    g = grade_answer(q, "随便什么答案")
    assert not g.is_correct


def test_grade_fail_closed_no_answer_no_keywords():
    """无作答且无 expected：双重缺失仍判错，绝不判对。"""
    q = build_question(_entry(keywords=()), mastery=0.6)
    assert not grade_answer(q, "").is_correct


# ── 判分：choice（标签精确匹配）────────────────────────────────────────

def test_choice_grade_correct_label():
    q = build_question(_entry(), distractors=[_entry(eid="D1", keywords=("甲", "乙"))], mastery=0.0)
    assert grade_answer(q, "A").is_correct
    assert grade_answer(q, "a").is_correct  # 容忍大小写
    assert grade_answer(q, " A ").is_correct  # 容忍空白


def test_choice_grade_fullwidth_label():
    """中文输入法的全角字母（U+FF21）必须判对。"""
    q = build_question(_entry(), distractors=[_entry(eid="D1", keywords=("甲", "乙"))], mastery=0.0)
    assert grade_answer(q, "Ａ").is_correct
    assert grade_answer(q, "Ａ ").is_correct


def test_choice_grade_bom_prefix():
    """粘贴内容带零宽/BOM 字符（U+FEFF）时仍判对。"""
    q = build_question(_entry(), distractors=[_entry(eid="D1", keywords=("甲", "乙"))], mastery=0.0)
    assert grade_answer(q, "\ufeffA").is_correct


def test_choice_grade_wrong_label():
    q = build_question(_entry(), distractors=[_entry(eid="D1", keywords=("甲", "乙"))], mastery=0.0)
    g = grade_answer(q, "B")
    assert not g.is_correct
    assert g.correct_label == "A"


def test_choice_grade_fail_closed_empty():
    q = build_question(_entry(), distractors=[_entry(eid="D1", keywords=("甲", "乙"))], mastery=0.0)
    assert not grade_answer(q, "").is_correct


def test_choice_grade_full_text_answer_not_accepted():
    """选择题只认标签——贴全文不算对（防抄选项文本）。"""
    q = build_question(_entry(), distractors=[_entry(eid="D1", keywords=("甲", "乙"))], mastery=0.0)
    assert not grade_answer(q, "数据库、表、主键").is_correct


# ── 反馈消息 ───────────────────────────────────────────────────────────

def test_feedback_names_missing_points():
    q = build_question(_entry(), mastery=0.6)
    g = grade_answer(q, "数据库就是把数据存起来")
    msg = build_feedback_message(g, q)
    assert "表" in msg and "主键" in msg
    assert "正确" not in msg


def test_feedback_positive():
    q = build_question(_entry(), mastery=0.6)
    g = grade_answer(q, "数据库以表为存储单元，主键唯一标识一行")
    assert "正确" in build_feedback_message(g, q)


def test_feedback_choice_wrong_reveals_correct_option():
    q = build_question(_entry(), distractors=[_entry(eid="D1", keywords=("甲", "乙"))], mastery=0.0)
    g = grade_answer(q, "B")
    msg = build_feedback_message(g, q)
    assert "A." in msg  # 反馈给出正确选项文本（教学常规，expected 仍不进题干）
