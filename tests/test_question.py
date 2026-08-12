"""question 节点：判分要点服务端校验（字符必须出自条目 content）+ 脚手架干扰项校验。"""

from core.agents.question import (
    MAX_EXPECTED_KEYWORDS,
    validate_distractors,
    validate_expected_keywords,
)

CONTENT = "关系型数据库以表为基本存储单元，主键唯一标识一行，外键建立表间引用。SQLite 是常见实现。"


def test_valid_keywords_pass():
    valid = validate_expected_keywords(["主键", "外键", "表"], CONTENT)
    assert valid == ["主键", "外键", "表"]


def test_hallucinated_keyword_dropped():
    """LLM 编造的超纲要点（字符不在 content）被丢弃。"""
    valid = validate_expected_keywords(["主键", "机器学习", "深度学习"], CONTENT)
    assert valid == ["主键"]


def test_empty_content_rejects_all():
    assert validate_expected_keywords(["主键"], "") == []


def test_blank_keywords_skipped():
    assert validate_expected_keywords(["", "   ", "主键"], CONTENT) == ["主键"]


def test_max_four_keywords():
    many = [f"要点{i}" for i in range(8)]
    content = "要点0 要点1 要点2 要点3 要点4 要点5 要点6 要点7"
    valid = validate_expected_keywords(many, content)
    assert len(valid) == MAX_EXPECTED_KEYWORDS


def test_latin_keyword_case_insensitive():
    """拉丁词大小写不敏感（content 里的 sqlite 可匹配 SQLite）。"""
    assert validate_expected_keywords(["sqlite"], CONTENT) == ["sqlite"]


# ── 脚手架选择题干扰项校验 ────────────────────────────────────────────

def test_distractors_drop_duplicate_and_correct_text():
    valid = validate_distractors(["甲", "甲", "正确答案", "乙", "丙", "丁"], "正确答案")
    assert valid == ["甲", "乙", "丙"]


def test_distractors_cap_at_three():
    valid = validate_distractors(["甲", "乙", "丙", "丁", "戊"], "X")
    assert len(valid) == 3


def test_distractors_blank_skipped():
    assert validate_distractors(["", "  ", "甲"], "X") == ["甲"]


def test_distractors_all_invalid_returns_empty():
    """全部与正确项相同或为空 → 空列表，由调用方回退确定性干扰项。"""
    assert validate_distractors(["X", "X", ""], "X") == []
