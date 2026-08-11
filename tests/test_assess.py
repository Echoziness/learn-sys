"""assess 确定性出题与判分：关键词覆盖率、fail-closed。"""

from core.assess import build_feedback_message, build_question, grade_answer
from core.plan import KnowledgeEntry


def _entry(eid="E1", title="关系型数据库", keywords=("数据库", "表", "主键")):
    return KnowledgeEntry(
        id=eid,
        title=title,
        content="内容",
        keywords=list(keywords),
    )


def test_question_prompt_contains_entry_title():
    q = build_question(_entry())
    assert q.question_type == "recall"
    assert "关系型数据库" in q.prompt
    assert q.expected_keywords == ("数据库", "表", "主键")


def test_grade_correct_when_all_keywords_present():
    q = build_question(_entry())
    g = grade_answer(q, "数据库以表为存储单元，主键唯一标识一行")
    assert g.is_correct
    assert g.keyword_coverage == 1.0
    assert g.missing == ()


def test_grade_partial_under_threshold():
    q = build_question(_entry())
    g = grade_answer(q, "数据库就是把数据存起来")
    assert not g.is_correct
    assert g.missing == ("表", "主键")


def test_grade_fail_closed_empty_answer():
    q = build_question(_entry())
    g = grade_answer(q, "")
    assert not g.is_correct
    assert g.keyword_coverage == 0.0


def test_grade_fail_closed_no_keywords():
    q = build_question(_entry(keywords=()))
    g = grade_answer(q, "随便什么答案")
    assert not g.is_correct


def test_grade_fail_closed_no_answer_no_keywords():
    """无作答且无 expected：双重缺失仍判错，绝不判对。"""
    q = build_question(_entry(keywords=()))
    assert not grade_answer(q, "").is_correct


def test_feedback_names_missing_points():
    q = build_question(_entry())
    g = grade_answer(q, "数据库就是把数据存起来")
    msg = build_feedback_message(g)
    assert "表" in msg and "主键" in msg
    assert "正确" not in msg


def test_feedback_positive():
    q = build_question(_entry())
    g = grade_answer(q, "数据库以表为存储单元，主键唯一标识一行")
    assert "正确" in build_feedback_message(g)
