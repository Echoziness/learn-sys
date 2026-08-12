"""作答管线（process_answer）：评估与裁决分离 + fail-closed 收口。

- answer 题总是送 LLM 评估（覆盖率不足的作答最有教学价值）；
- LLM 判 correct 但规则覆盖率不足 → 维持判错（LLM 无权绕过关键词底线）；
- 裁决被否决时评估也不采用 LLM 的（避免"答对了"误导）。
"""

from core.answer_pipeline import process_answer
from core.assess import build_question
from core.plan import KnowledgeEntry


class FakeFeedback:
    """FeedbackLLM 最小实现：返回预设裁决，记录调用次数。"""

    def __init__(self, verdict: str, evaluation: str = "LLM评估"):
        self._verdict = verdict
        self._evaluation = evaluation
        self.calls = 0

    async def chat_validated(self, messages, schema, model=None, **kwargs):  # noqa: ANN001
        self.calls += 1
        return schema(verdict=self._verdict, evaluation=self._evaluation)


def _answer_question():
    entry = KnowledgeEntry(
        id="E1", title="关系型数据库", content="内容", keywords=["数据库", "表", "主键"]
    )
    return build_question(entry, mastery=0.8)  # answer 题


async def test_answer_low_coverage_llm_verdict_correct_denied():
    """覆盖率不足时 LLM 判 correct 被否决：维持判错，评估不用 LLM 的。"""
    fb = FakeFeedback(verdict="correct", evaluation="你答得很好！")
    out = await process_answer(
        fb, _answer_question(), "数据库", [], min_coverage=0.6
    )
    assert fb.calls == 1  # 评估总是做
    assert out.is_correct is False  # fail-closed：LLM 无权绕过覆盖底线
    assert "你答得很好" not in out.evaluation  # 避免"答对了"的误导


async def test_answer_low_coverage_llm_verdict_incorrect_kept():
    """覆盖率不足且 LLM 判 incorrect：维持判错，评估用 LLM 的（教学价值）。"""
    fb = FakeFeedback(verdict="incorrect", evaluation="你混淆了主键与外键的概念")
    out = await process_answer(fb, _answer_question(), "数据库", [])
    assert out.is_correct is False
    assert "混淆" in out.evaluation
    assert out.llm_reviewed is True


async def test_answer_low_coverage_llm_partial_kept():
    fb = FakeFeedback(verdict="partial", evaluation="方向对但遗漏了主键")
    out = await process_answer(fb, _answer_question(), "数据库", [])
    assert out.is_correct is False
    assert "遗漏" in out.evaluation


async def test_answer_high_coverage_llm_correct_passes():
    fb = FakeFeedback(verdict="correct", evaluation="理解完整")
    out = await process_answer(fb, _answer_question(), "数据库 表 主键", [])
    assert out.is_correct is True
    assert out.evaluation == "理解完整"


async def test_answer_high_coverage_llm_partial_denies():
    fb = FakeFeedback(verdict="partial", evaluation="还差一点")
    out = await process_answer(fb, _answer_question(), "数据库 表 主键", [])
    assert out.is_correct is False


async def test_choice_correct_skips_llm():
    entry = KnowledgeEntry(id="E1", title="关系型数据库", content="内容", keywords=["数据库"])
    q = build_question(entry, mastery=0.0)  # choice 题
    fb = FakeFeedback(verdict="incorrect")
    out = await process_answer(fb, q, "A", [])
    assert fb.calls == 0
    assert out.is_correct is True
    assert out.llm_reviewed is False


async def test_choice_wrong_calls_llm():
    entry = KnowledgeEntry(id="E1", title="关系型数据库", content="内容", keywords=["数据库"])
    q = build_question(entry, mastery=0.0)
    fb = FakeFeedback(verdict="incorrect", evaluation="正确答案是 A")
    out = await process_answer(fb, q, "B", [])
    assert fb.calls == 1
    assert out.is_correct is False
    assert out.evaluation == "正确答案是 A"
