"""动态追问机制测试（2026-08-26）：与错题→脚手架同构的澄清管线。

覆盖：有效提问 → 判定+确认题生成 → 事件落流；作答不写掌握度、
不影响主轮号段与进度推导；无效提问 fail-closed；刷新恢复（pending_followup）；
重教作废未作答追问轮。
"""

import asyncio
from typing import Any

from tests.test_teach_loop import FakeProvider, _events, _profile, _setup

from core.agents.feedback import FeedbackOutput
from core.agents.question import FollowupOutput


class FollowupProvider(FakeProvider):
    """在 FakeProvider 基础上支持 FollowupOutput（按队列消费，可配判定结果）。"""

    def __init__(self, followups: list[FollowupOutput], feedbacks=None):
        super().__init__(feedbacks)
        self._followups = followups

    async def chat_validated(self, messages, schema, model=None, **kwargs) -> Any:
        if schema.__name__ == "FollowupOutput":
            assert self._followups, "followup 响应队列耗尽"
            self.calls.append("FollowupOutput")
            return self._followups.pop(0)
        return await super().chat_validated(messages, schema, model=model, **kwargs)


def _setup_followup(tmp_path, followups, feedbacks=None):
    """替换 provider 为支持追问的假实现（其余复用 test_teach_loop 基建）。"""
    loop, store = _setup(tmp_path, feedbacks=feedbacks)
    loop._provider = FollowupProvider(followups, feedbacks)  # type: ignore[assignment]
    return loop, store


def _valid_followup() -> FollowupOutput:
    return FollowupOutput(
        is_valid=True,
        reason="与本轮教学论断相关",
        question="关于甲的作用，下面哪个理解是正确的？",
        correct="甲是主题一的核心概念，乙是配套",
        distractors=["甲和乙没有任何关系", "乙才是主题一的核心概念"],
    )


def test_followup_valid_flow(tmp_path):
    """有效提问 → 确认题侧车轮 → 作答不写掌握度、主流程不受影响。"""
    feedbacks = [
        FeedbackOutput(verdict="correct", evaluation="选择题对", missed_requirements=[]),
        FeedbackOutput(verdict="correct", evaluation="追问确认对", missed_requirements=[]),
    ]
    loop, store = _setup_followup(tmp_path, [_valid_followup()], feedbacks=feedbacks)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))

    # 主题目照常（pending 主轮与追问通道互不干扰）
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q1.question_id == "q_E1_r1_choice"

    # 追问判定有效 → 确认题落侧车轮
    r = asyncio.run(loop.handle_followup(ctx, "E1", "甲到底起什么作用？"))
    assert r.valid and r.question is not None
    assert r.question.question_id == "q_E1_r2_followup"  # 侧车占用独立号段
    assert r.question.expected_label == "A"
    events = _events(store, ctx.session_id)
    assert events["followup_asked"][0]["valid"] is True
    assert "followup_offered" in events

    # 刷新恢复：pending_followup 可读
    pending = loop.pending_followup(ctx.session_id, "E1")
    assert pending is not None and pending["round_no"] == 2

    # 主轮判分不受追问轮占号影响（round_no 必须落在 1 而非 2）
    res = asyncio.run(loop.handle_answer(ctx, "E1", q1, q1.expected_label))
    assert res.round_no == 1

    # 追问作答：不写掌握度快照，决策语义不进主流程
    g = asyncio.run(loop.answer_followup(ctx, "E1", "A"))
    assert g.is_correct and g.round_no == 2
    assert store.load_mastery_history(ctx.session_id, "E1") == [True]
    assert "followup_graded" in _events(store, ctx.session_id)

    # 追问轮不进进度推导：needs_teaching 仍由主轮（choice 答对）决定
    progress = loop.progress(ctx.session_id, "E1")
    assert progress.needs_teaching is True  # choice 答对仍需教学（应用推进）
    assert not progress.scaffold_pending

    # 资源化衔接：分阶题归档含已作答追问轮
    asyncio.run(loop.teach_round(ctx, "E1"))
    q2 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q2.question_id == "q_E1_r3_answer"  # 追问占 2 后主轮顺延
    fb2 = FeedbackOutput(verdict="correct", evaluation="回答题对", missed_requirements=[])
    loop._provider._feedbacks.append(fb2)  # type: ignore[attr-defined]
    asyncio.run(loop.handle_answer(ctx, "E1", q2, "甲和乙"))
    packages = store.load_packages(ctx.session_id)
    qids = [q["question_id"] for q in packages[0]["questions"]]
    assert "q_E1_r2_followup" in qids


def test_followup_invalid_fail_closed(tmp_path):
    """无效提问：不生成确认题、不落轮，只留 followup_asked 事件。"""
    loop, store = _setup_followup(
        tmp_path,
        [FollowupOutput(is_valid=False, reason="与当前学习内容无关")],
    )
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))

    r = asyncio.run(loop.handle_followup(ctx, "E1", "今天天气怎么样？"))
    assert not r.valid and r.question is None and "无关" in r.reason
    assert loop.pending_followup(ctx.session_id, "E1") is None
    events = _events(store, ctx.session_id)
    assert events["followup_asked"][0]["valid"] is False
    assert "followup_offered" not in events


def test_followup_llm_failure_fail_closed(tmp_path):
    """LLM 异常 → 判无效（学生收到判定理由，不静默失败）。"""

    class BrokenProvider(FollowupProvider):
        async def chat_validated(self, messages, schema, model=None, **kwargs):
            if schema.__name__ == "FollowupOutput":
                raise RuntimeError("LLM 超时")
            return await FakeProvider.chat_validated(
                self, messages, schema, model=model, **kwargs
            )

    loop, store = _setup(tmp_path)
    loop._provider = BrokenProvider([])  # type: ignore[assignment]
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    r = asyncio.run(loop.handle_followup(ctx, "E1", "甲是什么？"))
    assert not r.valid and r.reason


def test_followup_replaced_and_invalidated_on_reteach(tmp_path):
    """新提问替换旧未作答确认题；重教作废未作答追问轮。"""
    loop, store = _setup_followup(tmp_path, [_valid_followup(), _valid_followup()])
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))

    asyncio.run(loop.handle_followup(ctx, "E1", "第一个疑问"))
    r2 = asyncio.run(loop.handle_followup(ctx, "E1", "第二个疑问"))
    assert r2.valid and r2.question is not None
    # 只剩一个未作答追问轮（旧的被替换）
    rows = [
        x for x in store.load_rounds(ctx.session_id, "E1")
        if x["decision"] == "followup" and x["answer"] is None
    ]
    assert len(rows) == 1

    # 重教作废未作答追问轮（删除后号段从剩余轮重算）
    asyncio.run(loop.teach_round(ctx, "E1"))
    assert loop.pending_followup(ctx.session_id, "E1") is None
    q = asyncio.run(loop.next_question(ctx, "E1"))
    assert q.question_id == "q_E1_r1_choice"
