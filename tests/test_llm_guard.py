"""LLM 层防护：孤立 surrogate 净化；feedback fail-closed 收紧。"""

import asyncio
from dataclasses import asdict

from core.agents.feedback import feedback_node
from core.assess import build_question
from core.llm import LLMProvider
from core.plan import KnowledgeEntry


class FailingProvider:
    """chat_validated 必然抛异常——用于 fail-closed 回退测试。"""

    async def chat_validated(self, messages, schema, model=None, **kwargs):
        raise RuntimeError("LLM unavailable")


class StubVerdictProvider:
    """返回预设裁决——用于矛盾检测测试。"""

    def __init__(self, verdict, missed=None, evaluation="评估"):
        self._verdict = verdict
        self._missed = missed or []
        self._evaluation = evaluation

    async def chat_validated(self, messages, schema, model=None, **kwargs):
        return schema(
            verdict=self._verdict,
            evaluation=self._evaluation,
            missed_requirements=self._missed,
        )


def test_sanitize_text_removes_lone_surrogates():
    text = "正常文本\ud800\udfff结束"
    cleaned = LLMProvider._sanitize_text(text)
    assert cleaned == "正常文本\ufffd\ufffd结束"
    cleaned.encode("utf-8")  # 不再抛 UnicodeEncodeError


def test_sanitize_text_keeps_normal():
    text = "关系型数据库 主键 SQL"
    assert LLMProvider._sanitize_text(text) == text


def _question_state():
    entry = KnowledgeEntry(
        id="E1", title="关系型数据库", content="主键唯一标识，外键关联", keywords=["主键", "外键"]
    )
    return build_question(entry, mastery=0.8)


def test_fallback_requires_full_coverage():
    """LLM 缺席时：覆盖率不足 1.0 判 incorrect（不 advance）。"""
    q = _question_state()
    fb = asyncio.run(
        feedback_node(
            {"question": asdict(q), "answer": "主键", "rule_coverage": 0.67},
            provider=FailingProvider(),
        )
    )
    assert fb["verdict"] == "incorrect"


def test_fallback_full_coverage_passes():
    q = _question_state()
    fb = asyncio.run(
        feedback_node(
            {"question": asdict(q), "answer": "主键", "rule_coverage": 1.0},
            provider=FailingProvider(),
        )
    )
    assert fb["verdict"] == "correct"


def test_fallback_empty_coverage_fails():
    q = _question_state()
    fb = asyncio.run(
        feedback_node(
            {"question": asdict(q), "answer": "不知道", "rule_coverage": 0.0},
            provider=FailingProvider(),
        )
    )
    assert fb["verdict"] == "incorrect"


def test_correct_with_missed_requirements_downgraded():
    """判 correct 但自报遗漏清单 → 服务端硬降级为 partial（防 LIMIT 式漏答放行）。"""
    q = _question_state()
    fb = asyncio.run(
        feedback_node(
            {"question": asdict(q), "answer": "select * from goods order by sales desc"},
            provider=StubVerdictProvider(
                verdict="correct", missed=["LIMIT 前5件"], evaluation="整体不错"
            ),
        )
    )
    assert fb["verdict"] == "partial"
    assert fb["evaluation"] == "整体不错"


def test_correct_without_missed_kept():
    q = _question_state()
    fb = asyncio.run(
        feedback_node(
            {"question": asdict(q), "answer": "完整作答"},
            provider=StubVerdictProvider(verdict="correct", missed=[], evaluation="完整"),
        )
    )
    assert fb["verdict"] == "correct"


def test_partial_with_missed_kept():
    q = _question_state()
    fb = asyncio.run(
        feedback_node(
            {"question": asdict(q), "answer": "部分作答"},
            provider=StubVerdictProvider(
                verdict="partial", missed=["LIMIT"], evaluation="漏了 LIMIT"
            ),
        )
    )
    assert fb["verdict"] == "partial"
