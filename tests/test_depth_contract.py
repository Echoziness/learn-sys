"""深度契约 + 错因回流：教学加深 → 题目跟随 → 审核分级。

覆盖三个新机制：
- generate 注入 retry_context（错因回流，prompt 出现【学生错因与上轮作答】段）；
- question 注入 taught_claims（出题深度契约，prompt 出现【本轮教学内容】段）；
- DraftClaim claim_type 分层（core 默认 / extension），规则层对扩展论断同样拦无效引用。
"""

import asyncio
from typing import Any

from core.agents.generate import generate_node
from core.agents.question import QuestionInput, question_node
from core.agents.review import rule_check
from core.llm import LLMProvider
from core.state import AgentState, DraftClaim, RetrievedEntry

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


GENERATE_OK = (
    '{"draft": [{"claim_index": 1, "text": "表是基本存储单元。", '
    '"evidence_ids": ["BDA-DB-001"], "claim_type": "core"}]}'
)
QUESTION_OK = '{"question": "统计表有重复行怎么解决？", "expected_keywords": ["主键"]}'


def _base_state() -> AgentState:
    entry = RetrievedEntry(
        id="BDA-DB-001", title="关系型数据库", content=ENTRY["content"], score=1.0
    )
    return {
        "retrieved_entries": [entry],
        "difficulty_level": "beginner",
        "profile_summary": "初学者",
        "outline": {},
    }


def test_generate_injects_retry_context():
    """重教轮：错因文本进入生成 prompt，要求针对性回应。"""
    provider = CaptureProvider([GENERATE_OK])
    retry_context = "题目：如何设计选课表\n学生作答：外键放在学生表\n评估：方向反了"
    state: AgentState = {**_base_state(), "retry_context": retry_context}
    asyncio.run(generate_node(state, provider=provider))
    assert "【学生错因与上轮作答】" in provider.prompt
    assert "方向反了" in provider.prompt
    assert '"claim_type": "core"' in provider.prompt  # 输出 schema 含分层字段


def test_generate_omits_retry_section_without_context():
    """首轮：无错因上下文时 prompt 不含重教段。"""
    provider = CaptureProvider([GENERATE_OK])
    asyncio.run(generate_node(_base_state(), provider=provider))
    assert "学生作答：" not in provider.prompt
    assert "【学生错因与上轮作答】\n" not in provider.prompt


def test_question_injects_taught_claims():
    """深度契约：本轮教学论断进入出题 prompt，作为题目难度上限。"""
    provider = CaptureProvider([QUESTION_OK])
    state: QuestionInput = {
        "entry": ENTRY,
        "taught_claims": ["表由行和列组成，主键唯一标识一行，外键引用另一表主键。"],
    }
    out = asyncio.run(question_node(state, provider=provider))
    assert "【本轮教学内容】" in provider.prompt
    assert "表由行和列组成" in provider.prompt
    assert out["question"] == "统计表有重复行怎么解决？"


def test_question_without_claims_fallback_hint():
    """无教学论断时给出回退提示，防止出题超纲。"""
    provider = CaptureProvider([QUESTION_OK])
    asyncio.run(question_node({"entry": ENTRY}, provider=provider))
    assert "基于条目内容提问" in provider.prompt


def test_draft_claim_defaults_to_core():
    claim = DraftClaim(claim_index=1, text="论断", evidence_ids=["E1"])
    assert claim.claim_type == "core"


def test_rule_check_flags_extension_missing_evidence():
    """extension 论断也不能引用不存在的条目——规则层不分类型一律拦截。"""
    draft = [
        DraftClaim(claim_index=1, text="扩展讲解", evidence_ids=["E1"], claim_type="extension"),
    ]
    cited = [RetrievedEntry(id="E1", title="条目一", content="原文", score=0.9)]
    assert rule_check(draft, cited) == ([], set())

    draft[0] = DraftClaim(claim_index=1, text="扩展讲解", evidence_ids=["E404"], claim_type="extension")
    notes, flagged = rule_check(draft, cited)
    assert flagged == {1}
    assert notes[0].verdict == "unsupported"
