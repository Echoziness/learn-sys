"""动态追问机制测试（2026-08-28 设计回归）：记录困惑 → 解答 → 下轮注入。

覆盖：有效提问 → 困惑记录落库 + 直接解答（不即时出题）→ 事件落流；
困惑注入下一轮教学（错因回流同通道）；教学消化后不再注入；巩固模式下
困惑优先触发教学；无效提问/LLM 失败 fail-closed；困惑进 distill 原料。
"""

import asyncio
from typing import Any

from tests.test_teach_loop import FakeProvider, _events, _profile, _setup

from core.agents.feedback import FeedbackOutput
from core.agents.question import (
    FollowupOutput,
    _format_claims,
    _format_current_question,
)
from core.export_pipeline import collect_fail_material


class FollowupProvider(FakeProvider):
    """在 FakeProvider 基础上支持 FollowupOutput（按队列消费，可配判定结果）。"""

    def __init__(self, followups: list[FollowupOutput], feedbacks=None):
        super().__init__(feedbacks)
        self._followups = followups

    async def chat_validated(self, messages, schema, model=None, **kwargs) -> Any:
        if schema.__name__ == "FollowupOutput":
            assert self._followups, "followup 响应队列耗尽"
            self.calls.append("FollowupOutput")
            self.prompts.append(str(messages[0].get("content", "")))
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
        answer="甲是主题一的核心概念，乙是配套使用——甲负责定义，乙负责配合甲完成操作。",
    )


def test_followup_valid_records_and_answers(tmp_path):
    """有效提问 → 困惑记录落库 + 直接解答，不即时出题、不写掌握度。"""
    loop, store = _setup_followup(tmp_path, [_valid_followup()])
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))

    r = asyncio.run(loop.handle_followup(ctx, "E1", "甲到底起什么作用？"))
    assert r.valid and r.answer is not None and "核心概念" in r.answer

    # 困惑记录：decision=followup，prompt=学生疑问，answer=系统解答
    rounds = store.load_rounds(ctx.session_id, "E1")
    fu = [x for x in rounds if x["decision"] == "followup"]
    assert len(fu) == 1
    assert fu[0]["question"]["question_type"] == "followup"
    assert fu[0]["question"]["prompt"] == "甲到底起什么作用？"
    assert fu[0]["answer"] == r.answer

    # 事件落流：含解答，且不再有即时出题/判分事件
    events = _events(store, ctx.session_id)
    assert events["followup_asked"][0]["valid"] is True
    assert events["followup_asked"][0]["answer"] == r.answer
    assert "followup_offered" not in events
    assert "followup_graded" not in events

    # 不写掌握度；主轮号段在困惑记录后顺延（轮号仅编码，无语义影响）
    assert store.load_mastery_history(ctx.session_id, "E1") == []
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q1.question_id == "q_E1_r3_choice"

    # 刷新恢复：最近困惑记录可读
    last = loop.last_followup_record(ctx.session_id, "E1")
    assert last is not None and last["question"] == "甲到底起什么作用？"


def test_followup_injected_into_next_teach(tmp_path):
    """未消化困惑注入下一轮教学（错因回流同通道，followup_context）。"""
    loop, store = _setup_followup(tmp_path, [_valid_followup()])
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    asyncio.run(loop.handle_followup(ctx, "E1", "甲到底起什么作用？"))

    assert len(loop.pending_followups(ctx.session_id, "E1")) == 1

    # 下一轮教学：困惑进 generate 的 state（FakeGraph.states 可查）
    asyncio.run(loop.teach_round(ctx, "E1"))
    graph_states = loop._graph.states  # type: ignore[attr-defined]
    assert "followup_context" in graph_states[-1]
    assert "甲到底起什么作用？" in graph_states[-1]["followup_context"]
    assert "当时已给出的解答" in graph_states[-1]["followup_context"]

    # 教学完成后困惑被消化（轮号推导，无状态标记）
    assert loop.pending_followups(ctx.session_id, "E1") == []
    # 再教一轮不重复注入
    asyncio.run(loop.teach_round(ctx, "E1"))
    assert "followup_context" not in loop._graph.states[-1]  # type: ignore[attr-defined]


def test_followup_forces_teaching_over_consolidation(tmp_path):
    """巩固模式让位困惑：answer 答对未达门槛本可跳过教学，有未消化困惑则必须教。"""
    feedbacks = [
        # choice 答错也走 LLM 复核（解释错因）→ 消费一条；最终 answer 复核一条
        FeedbackOutput(verdict="incorrect", evaluation="choice 错", missed_requirements=[]),
        FeedbackOutput(verdict="correct", evaluation="回答题对", missed_requirements=[]),
    ]
    loop, store = _setup_followup(tmp_path, [_valid_followup()], feedbacks=feedbacks)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))

    # choice 答错→重教→答对（[F,T]→0.588）→重教→answer 答对（[F,T,T]→0.696 未达门槛）→巩固
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    wrong_label = "A" if q1.expected_label != "A" else "B"
    asyncio.run(loop.handle_answer(ctx, "E1", q1, wrong_label))  # choice 答错 → 重教（规则判，无 feedback）
    asyncio.run(loop.teach_round(ctx, "E1"))  # retry 重教
    q2 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q2.question_type == "choice"  # 掌握度 0 仍是识别层
    asyncio.run(loop.handle_answer(ctx, "E1", q2, q2.expected_label))  # 答对 → [F,T] 0.588 未达标→重教
    asyncio.run(loop.teach_round(ctx, "E1"))
    q3 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q3.question_type == "answer"  # 0.588 ≥ 0.5 → 回忆层
    asyncio.run(loop.handle_answer(ctx, "E1", q3, "甲、乙"))  # 答对但未达门槛

    progress = loop.progress(ctx.session_id, "E1")
    assert progress.needs_teaching is False  # 巩固模式：本可跳过教学

    # 困惑记录后，端点判定式（与 routes 同逻辑）翻转为必须教学
    asyncio.run(loop.handle_followup(ctx, "E1", "乙为什么是配套的？"))
    assert progress.needs_teaching or bool(loop.pending_followups(ctx.session_id, "E1"))


def test_followup_invalid_fail_closed(tmp_path):
    """无效提问：不落困惑记录、无解答，只留 followup_asked 事件（valid=false）。"""
    loop, store = _setup_followup(
        tmp_path,
        [FollowupOutput(is_valid=False, reason="与当前学习内容无关")],
    )
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))

    r = asyncio.run(loop.handle_followup(ctx, "E1", "今天天气怎么样？"))
    assert not r.valid and r.answer is None and "无关" in r.reason
    assert loop.pending_followups(ctx.session_id, "E1") == []
    assert loop.last_followup_record(ctx.session_id, "E1") is None
    events = _events(store, ctx.session_id)
    assert events["followup_asked"][0]["valid"] is False


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


def test_followup_confusion_enters_distill_material(tmp_path):
    """困惑记录进 distill 原料：学生疑问被采集，系统解答不被误当学生作答。"""
    loop, store = _setup_followup(tmp_path, [_valid_followup()])
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    result = asyncio.run(loop.handle_followup(ctx, "E1", "甲到底起什么作用？"))

    wrong, distractors = collect_fail_material(store, ctx.session_id, "E1")
    assert any(w["prompt"] == "甲到底起什么作用？" for w in wrong)
    # 系统解答绝不作为"学生作答"出现在任何原料里
    assert result.answer is not None
    assert all(result.answer not in w["answer"] for w in wrong)
    assert distractors == []  # 新式困惑记录无选项


# ── 追问上下文注入辅助函数（保留覆盖）──────────────────────────────────


def test_format_claims_with_round_no():
    """论断带 round_no 时渲染轮次标注，不带时不显示。"""
    claims = [
        {"text": "论断A", "claim_type": "core", "round_no": 1},
        {"text": "论断B", "claim_type": "extension", "round_no": 3},
        {"text": "论断C", "claim_type": "core"},  # 无 round_no
    ]
    result = _format_claims(claims)
    assert "第 1 轮 · core" in result
    assert "第 3 轮 · extension" in result
    assert "论断A" in result and "论断C" in result
    assert "第 轮" not in result
    lines = result.strip().split("\n")
    assert not lines[2].startswith("- [第")  # 论断C 无轮次标注


def test_format_current_question_with_answer():
    """当前题目渲染：题干 + 选项 + 学生作答 + 判定。"""
    q = {
        "question_id": "q_E1_r2_answer",
        "question_type": "answer",
        "prompt": "请说明主键的作用",
        "options": [],
        "student_answer": "主键可以加密数据",
        "is_correct": False,
    }
    result = _format_current_question(q)
    assert "【当前题目】（answer）" in result
    assert "请说明主键的作用" in result
    assert "【学生作答】主键可以加密数据" in result
    assert "判定：错误" in result


def test_format_current_question_empty():
    """空 dict → 空字符串（不渲染）。"""
    assert _format_current_question({}) == ""


def test_handle_followup_injects_current_question(tmp_path):
    """handle_followup 注入 current_question + 带轮次标注的 claims 到 LLM prompt。"""
    feedbacks = [
        FeedbackOutput(verdict="correct", evaluation="choice 对", missed_requirements=[]),
    ]
    loop, store = _setup_followup(tmp_path, [_valid_followup()], feedbacks=feedbacks)
    provider = loop._provider
    assert isinstance(provider, FollowupProvider)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    asyncio.run(loop.handle_answer(ctx, "E1", q1, q1.expected_label))

    asyncio.run(loop.handle_followup(ctx, "E1", "这道题的选项B是什么意思？"))
    followup_prompts = [
        p for name, p in zip(provider.calls, provider.prompts, strict=False)
        if name == "FollowupOutput"
    ]
    assert len(followup_prompts) == 1
    prompt = followup_prompts[0]
    assert "【当前题目】" in prompt
    assert "第 1 轮" in prompt
