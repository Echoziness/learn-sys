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


def test_max_five_keywords():
    many = [f"要点{i}" for i in range(8)]
    content = "要点0 要点1 要点2 要点3 要点4 要点5 要点6 要点7"
    valid = validate_expected_keywords(many, content)
    assert len(valid) == MAX_EXPECTED_KEYWORDS == 5


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


# ── choice 概念辨析题服务端校验（Fix 1，2026-08-23）──────────────────────

from core.agents.question import ChoiceQuestionOutput, validate_choice_question  # noqa: E402

ENTRY = {"id": "E1", "title": "主键与外键", "content": CONTENT}
CLAIMS = [{"text": "主键唯一标识表中的一行记录", "claim_type": "core"}]


def _choice(**overrides):
    base = {
        "question": "关于主键的作用，下列说法正确的是？",
        "correct": "主键用于唯一标识表中的一行记录",
        "distractors": [
            "主键的作用是让一行记录可以重复出现",
            "主键可以标识多行相同的记录",
            "主键主要用于加密表中的数据",
        ],
    }
    base.update(overrides)
    return ChoiceQuestionOutput(**base)


def test_choice_valid_output_passes():
    out = validate_choice_question(_choice(), ENTRY, CLAIMS)
    assert out is not None
    assert out.question.startswith("关于主键")


def test_choice_keyword_pile_stem_rejected():
    """题干过短（如旧式元数据题）被拒——最低长度防线。"""
    out = validate_choice_question(_choice(question="哪个对？"), ENTRY, CLAIMS)
    assert out is None


def test_choice_offtopic_correct_rejected():
    """正确项与条目/论断零词重叠（跑题）被拒。"""
    out = validate_choice_question(
        _choice(correct="机器学习模型需要大量标注数据训练"), ENTRY, CLAIMS
    )
    assert out is None


def test_choice_offtopic_distractor_rejected():
    """干扰项跑题（与源零重叠）被拒——误解项必须仍在讨论同一概念。"""
    out = validate_choice_question(
        _choice(distractors=["量子计算正在进行纠错研究", "主键可以重复", "主键标识多行"])
        , ENTRY, CLAIMS
    )
    assert out is None


def test_choice_duplicate_distractors_trimmed():
    """重复/与正确项相同的干扰项被裁剪；不足 2 个则整体回退。"""
    out = validate_choice_question(
        _choice(distractors=["主键可以重复", "主键可以重复", "主键标识多行"]), ENTRY, CLAIMS
    )
    assert out is not None
    assert len(out.distractors) == 2
    assert validate_choice_question(
        _choice(distractors=["主键可以重复", "主键可以重复"]), ENTRY, CLAIMS
    ) is None
