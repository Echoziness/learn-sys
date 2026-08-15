"""上下文工程修复（2026-08-15）的行为契约测试。

覆盖六个机制：
- 出题降维契约：失败信号注入 question prompt（必须降维 + 遗漏要点）；
- 防重考：已出题干进上下文；
- expected 多源校验：场景实例词出自题干即可通过（同义判对的前提）；
- 脚手架完整生成校验：结构 + 词重叠 + 干扰项互异；
- 重教去重：taught_previously 进 generate prompt；
- 模板题不泄题：fallback 题干不得剧透判分要点。
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.agents.generate import generate_node
from core.agents.question import (
    ScaffoldOutput,
    question_node,
    validate_expected_keywords,
    validate_scaffold,
)
from core.assess import _build_answer
from core.llm import LLMProvider
from core.plan import KnowledgeEntry
from core.state import AgentState, RetrievedEntry

ENTRY: dict[str, Any] = {
    "id": "BDA-DB-001",
    "title": "关系型数据库基本概念",
    "content": "关系型数据库以表为基本存储单元，主键唯一标识一行，外键建立表间引用。",
    "keywords": ["表", "主键", "外键"],
}


class CaptureProvider(LLMProvider):
    """不触网：返回预设 JSON，捕获最后一次 prompt。"""

    def __init__(self, script: list[str]):
        self._script = list(script)
        self.prompt = ""

    async def chat_json(self, messages, model=None, **kwargs):  # noqa: ANN001
        self.prompt = messages[0]["content"]
        return self._script.pop(0)


QUESTION_OK = '{"question": "学生表用什么保证记录不重复？", "expected_keywords": ["主键"]}'
GENERATE_OK = (
    '{"draft": [{"claim_index": 1, "text": "表是基本存储单元。", '
    '"evidence_ids": ["BDA-DB-001"], "claim_type": "core"}]}'
)


# ── 出题降维契约 ──────────────────────────────────────────────────────


def test_retry_signal_injects_downgrade_contract():
    """失败信号注入：prompt 出现降维指令与遗漏要点文本。"""
    provider = CaptureProvider([QUESTION_OK])
    asyncio.run(
        question_node(
            {
                "entry": ENTRY,
                "taught_claims": [{"text": "主键唯一标识一行", "claim_type": "core"}],
                "retry": {
                    "missed_requirements": ["未提及参照完整性"],
                    "recent_wrong_count": 1,
                },
            },
            provider=provider,  # type: ignore[arg-type]
        )
    )
    assert "必须降维" in provider.prompt
    assert "未提及参照完整性" in provider.prompt
    assert "连续错 1 次" in provider.prompt


def test_no_retry_no_downgrade_section():
    """无失败信号：prompt 不出现降维段（首题综合提问不受限）。"""
    provider = CaptureProvider([QUESTION_OK])
    asyncio.run(
        question_node(
            {"entry": ENTRY, "taught_claims": [{"text": "主键唯一标识一行", "claim_type": "core"}]},
            provider=provider,  # type: ignore[arg-type]
        )
    )
    assert "必须降维" not in provider.prompt


def test_previous_questions_injected():
    """防重考：已出题干进上下文。"""
    provider = CaptureProvider([QUESTION_OK])
    asyncio.run(
        question_node(
            {
                "entry": ENTRY,
                "taught_claims": [{"text": "主键唯一标识一行", "claim_type": "core"}],
                "previous_questions": ["旧题干一", "旧题干二"],
            },
            provider=provider,  # type: ignore[arg-type]
        )
    )
    assert "旧题干一" in provider.prompt
    assert "旧题干二" in provider.prompt
    assert "禁止换皮重考" in provider.prompt


def test_claim_type_rendered_in_prompt():
    """教学论断分层渲染：core/extension 标注进上下文。"""
    provider = CaptureProvider([QUESTION_OK])
    asyncio.run(
        question_node(
            {
                "entry": ENTRY,
                "taught_claims": [
                    {"text": "主键唯一标识一行", "claim_type": "core"},
                    {"text": "孤儿记录的成因", "claim_type": "extension"},
                ],
            },
            provider=provider,  # type: ignore[arg-type]
        )
    )
    assert "[core] 主键唯一标识一行" in provider.prompt
    assert "[extension] 孤儿记录的成因" in provider.prompt


# ── expected 多源校验（同义判对的前提）────────────────────────────────


def test_expected_keyword_from_question_text_passes():
    """场景实例词出自题干（不在 content）也可作判分要点。"""
    valid = validate_expected_keywords(["主键", "学号"], ENTRY["content"], "学生表用学号标识每行")
    assert valid == ["主键", "学号"]


def test_expected_keyword_from_neither_source_dropped():
    """既不在 content 也不在题干的编造要点被丢弃。"""
    valid = validate_expected_keywords(["主键", "区块链"], ENTRY["content"], "学生表用学号标识")
    assert valid == ["主键"]


# ── 脚手架校验 ────────────────────────────────────────────────────────


def _scaffold_entry() -> dict[str, Any]:
    return ENTRY


def test_scaffold_valid_output_passes():
    out = ScaffoldOutput(
        question="关于主键，下面哪个理解是正确的？",
        correct="主键唯一标识一行，如学号标识学生",
        distractors=["镜像错误理解", "常见误解二", "多余项"],
    )
    v = validate_scaffold(out, _scaffold_entry(), [{"text": "主键唯一标识一行", "claim_type": "core"}])
    assert v is not None
    assert v.distractors == ["镜像错误理解", "常见误解二", "多余项"]


def test_scaffold_offtopic_correct_rejected():
    """正确项与条目/论断零词重叠（完全跑题）→ 整体拒绝，回退确定性。"""
    out = ScaffoldOutput(
        question="关于股票投资，下面哪个理解是正确的？",
        correct="分散投资能降低非系统性风险",
        distractors=["集中持仓波动大", "现金为王"],
    )
    assert validate_scaffold(out, _scaffold_entry(), []) is None


def test_scaffold_too_few_distractors_rejected():
    out = ScaffoldOutput(
        question="关于主键，下面哪个理解是正确的？",
        correct="主键唯一标识一行",
        distractors=["只有一个干扰项"],
    )
    assert validate_scaffold(out, _scaffold_entry(), []) is None


def test_scaffold_blank_or_short_rejected():
    assert (
        validate_scaffold(
            ScaffoldOutput(question="太短", correct="主键唯一标识一行", distractors=["误解一", "误解二"]),
            _scaffold_entry(),
            [],
        )
        is None
    )


# ── 重教去重 ──────────────────────────────────────────────────────────


def test_generate_injects_taught_previously():
    """重教去重：已教论断进 generate prompt，带禁止复读指令。"""
    provider = CaptureProvider([GENERATE_OK])
    state: AgentState = {
        "retrieved_entries": [
            RetrievedEntry(id="BDA-DB-001", title="关系型数据库", content=ENTRY["content"], score=1.0)
        ],
        "anchor_entry": KnowledgeEntry(
            id="BDA-DB-001", title="关系型数据库", content=ENTRY["content"], keywords=["表"]
        ),
        "difficulty_level": "beginner",
        "profile_summary": "初学者",
        "taught_previously": ["表由行和列组成", "主键唯一标识一行"],
    }
    asyncio.run(generate_node(state, provider=provider))  # type: ignore[arg-type]
    assert "已教内容" in provider.prompt
    assert "禁止复读" in provider.prompt
    assert "表由行和列组成" in provider.prompt


def test_generate_without_previously_omits_section():
    provider = CaptureProvider([GENERATE_OK])
    state: AgentState = {
        "retrieved_entries": [
            RetrievedEntry(id="BDA-DB-001", title="关系型数据库", content=ENTRY["content"], score=1.0)
        ],
        "difficulty_level": "beginner",
        "profile_summary": "初学者",
    }
    asyncio.run(generate_node(state, provider=provider))  # type: ignore[arg-type]
    assert "已教内容" not in provider.prompt


# ── 模板题不泄题 ──────────────────────────────────────────────────────


def test_fallback_answer_template_does_not_leak_keywords():
    entry = KnowledgeEntry(
        id="E1", title="主题", content="甲乙", keywords=["机密要点甲", "机密要点乙"]
    )
    q = _build_answer("q_E1", entry)
    for kw in entry.keywords:
        assert kw not in q.prompt
