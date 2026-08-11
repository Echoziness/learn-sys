"""question 节点：判分要点服务端校验（字符必须出自条目 content）。"""

from core.agents.question import MAX_EXPECTED_KEYWORDS, validate_expected_keywords

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
