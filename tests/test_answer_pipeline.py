"""作答管线（process_answer）：评估与裁决分离 + 裁决权归属。

- answer 题总是送 LLM 评估（覆盖率不足的作答最有教学价值）；
- 裁决权归属 LLM 题意核对（2026-08-15 收口修订）：同义表达（用实例/
  通俗说法）判 correct 采纳——关键词覆盖是字符级代理指标，测不了同义；
- 防放水三层：矛盾检测（correct+missed→partial，feedback_node 内）、
  题意核对清单、后续轮兜底；规则覆盖率落 grade 作审计信号。
"""

from core.answer_pipeline import process_answer
from core.assess import build_question
from core.plan import KnowledgeEntry


class FakeFeedback:
    """FeedbackLLM 最小实现：返回预设裁决，记录调用次数。"""

    def __init__(self, verdict: str, evaluation: str = "LLM评估", missed: list | None = None):
        self._verdict = verdict
        self._evaluation = evaluation
        self._missed = missed or []
        self.calls = 0

    async def chat_validated(self, messages, schema, model=None, **kwargs):  # noqa: ANN001
        self.calls += 1
        return schema(verdict=self._verdict, evaluation=self._evaluation,
                      missed_requirements=self._missed)


def _answer_question():
    entry = KnowledgeEntry(
        id="E1", title="关系型数据库", content="内容", keywords=["数据库", "表", "主键"]
    )
    return build_question(entry, mastery=0.8)  # answer 题


async def test_answer_low_coverage_llm_verdict_correct_accepted():
    """同义表达：覆盖率不足但 LLM 题意核对判 correct → 采纳（判定看理解）。"""
    fb = FakeFeedback(verdict="correct", evaluation="你说的'学号唯一'正是在说主键的作用")
    out = await process_answer(
        fb, _answer_question(), "用学号每个学生一个不重复", [], min_coverage=0.6
    )
    assert fb.calls == 1  # 评估总是做
    assert out.is_correct is True  # 裁决权在 LLM 题意核对
    assert "学号" in out.evaluation  # 评估采用 LLM 的（教行话）
    assert out.grade.keyword_coverage < 0.6  # 规则覆盖率仍落 grade 作审计


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
