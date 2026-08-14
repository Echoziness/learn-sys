"""teach_loop 状态机集成测试：fake 图 + fake LLM + 真实 SessionStore。

覆盖：诊断/切片落库 → 教学轮事件 → 题型阶梯（choice→answer）→ 脚手架状态机
→ 决策与掌握度沉淀 → advance 时资源包组装（三形态 + 进阶标记）。
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Literal

import sqlite_vec
from scripts.init_db import SCHEMA

from core.agents.diagnose import DiagnoseOutput
from core.agents.feedback import FeedbackOutput
from core.agents.question import QuestionOutput, ScaffoldOutput
from core.config import Settings
from core.plan import KnowledgeEntry
from core.session import SessionStore
from core.state import DraftClaim, LearnerProfile, ReviewNote
from core.teach_loop import TeachLoop

ClaimType = Literal["core", "extension", "procedure_guide"]


class FakeProvider:
    """按 schema 类型分发的 LLM 假实现；feedback 响应按队列依次消费。"""

    def __init__(self, feedbacks: list[FeedbackOutput] | None = None):
        self._feedbacks = feedbacks or []
        self.calls: list[str] = []

    async def chat_validated(self, messages, schema, model=None, **kwargs):
        name = schema.__name__
        self.calls.append(name)
        if name == "DiagnoseOutput":
            return DiagnoseOutput(
                gaps=["E1"], gap_ids=["E1"], profile_summary="测试摘要", difficulty_level="beginner"
            )
        if name == "QuestionOutput":
            return QuestionOutput(
                question="结合场景说明 E1 的要点？", expected_keywords=["甲", "乙"]
            )
        if name == "ScaffoldOutput":
            return ScaffoldOutput(distractors=["镜像错误理解", "无关项"])
        if name == "FeedbackOutput":
            assert self._feedbacks, "feedback 响应队列耗尽"
            return self._feedbacks.pop(0)
        raise AssertionError(f"未预期的 schema: {name}")


class FakeGraph:
    """教学子图假实现：astream 依次产出 retrieve/generate/review 三节点更新。"""

    def __init__(self, claim_type: ClaimType = "core"):
        self._claim_type: ClaimType = claim_type
        self.states: list[dict] = []

    def _outputs(self, state: dict) -> list[dict]:
        from core.state import RetrievedEntry

        claims = [
            DraftClaim(claim_index=1, text="论断一", evidence_ids=["E1"], claim_type=self._claim_type),
            DraftClaim(claim_index=2, text="论断二", evidence_ids=["E1"]),
        ]
        reviews = [
            ReviewNote(claim_index=1, verdict="supported", reason="支持"),
            ReviewNote(claim_index=2, verdict="supported", reason="支持"),
        ]
        return [
            {"retrieve": {
                "retrieved_entries": [RetrievedEntry(id="E1", title="E1", content="c", score=0.9)],
                "uncovered_gaps": [],
            }},
            {"generate": {"draft": claims, "cited_entries": [
                RetrievedEntry(id="E1", title="E1", content="c", score=0.9)
            ]}},
            {"review": {"review_history": reviews, "review_round": 1, "last_review_feedback": ""}},
        ]

    async def astream(self, state, stream_mode="updates"):
        self.states.append(state)
        for update in self._outputs(state):
            yield update


def _entry() -> KnowledgeEntry:
    return KnowledgeEntry(
        id="E1", title="主题一", content="甲乙内容", keywords=["甲", "乙"], difficulty=1,
        knowledge_type="procedure",
    )


def _settings() -> Settings:
    return Settings(
        database_path="unused", seed_dir="unused",
        diagnose_model=None, generate_model=None, review_model=None,
        feedback_model=None, question_model=None,
    )


def _setup(
    tmp_path: Path,
    *,
    feedbacks: list[FeedbackOutput] | None = None,
    claim_type: ClaimType = "core",
):
    db_path = str(tmp_path / "t.db")
    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.executescript(SCHEMA.replace("{vec_dim}", "8"))
    db.close()
    store = SessionStore(db_path)
    provider = FakeProvider(feedbacks)
    loop = TeachLoop(
        graph=FakeGraph(claim_type), provider=provider, store=store,  # type: ignore[arg-type]
        settings=_settings(), entries=[_entry()],
    )
    return loop, store


def _profile() -> LearnerProfile:
    return LearnerProfile(background={"goal": "测试"}, mastery={}, style_tags=[])


def _events(store: SessionStore, sid: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in store.load_events(sid):
        out.setdefault(e.event_type, []).append(e.payload)
    return out


def test_full_session_flow(tmp_path):
    feedbacks = [
        FeedbackOutput(verdict="correct", evaluation="R1 选择题对", missed_requirements=[]),
        FeedbackOutput(verdict="correct", evaluation="R2 回答题对", missed_requirements=[]),
    ]
    loop, store = _setup(tmp_path, feedbacks=feedbacks, claim_type="procedure_guide")
    # FakeGraph 的两条论断 claim_type 由参数控制第一条；指南提取需要 procedure_guide
    ctx = asyncio.run(loop.start_session("u1", _profile()))

    session_row = store.get_session(ctx.session_id)
    assert session_row is not None and session_row["gap_ids"] == ["E1"]
    assert [t.entry_id for t in ctx.plan.topics] == ["E1"]
    events = _events(store, ctx.session_id)
    assert "session_start" in events and "diagnose_done" in events and "plan_done" in events

    # R1：教学 → 选择题（mastery 0 → choice）
    teach1 = asyncio.run(loop.teach_round(ctx, "E1"))
    assert teach1.round_no == 1 and not teach1.is_retry
    events = _events(store, ctx.session_id)
    for et in ("topic_start", "retrieve_done", "generate_done", "review_done", "teach_delivered"):
        assert et in events, f"缺事件 {et}"

    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q1.question_type == "choice"
    # 幂等：重复出题复用 pending 轮
    q1_again = asyncio.run(loop.next_question(ctx, "E1"))
    assert q1_again.prompt == q1.prompt
    # expected 不进事件（学生视野隔离）
    q_events = _events(store, ctx.session_id)["question_built"]
    assert "expected" not in str(q_events)

    r1 = asyncio.run(loop.handle_answer(ctx, "E1", q1, q1.expected_label))
    assert r1.decision == "retry"  # mastery 0.5（单次封顶）< 0.7
    assert store.load_mastery_history(ctx.session_id, "E1") == [True]

    # R2：重教（错因回流注入）→ 回答题（mastery 0.5 ≥ 0.5）
    teach2 = asyncio.run(loop.teach_round(ctx, "E1"))
    assert teach2.round_no == 2 and teach2.is_retry
    assert "题目" in loop.progress(ctx.session_id, "E1").retry_context
    q2 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q2.question_type == "answer" and q2.expected_keywords == ("甲", "乙")

    r2 = asyncio.run(loop.handle_answer(ctx, "E1", q2, "甲和乙都覆盖"))
    assert r2.decision == "advance"  # [T,T] → 0.8 ≥ 0.7
    assert store.load_mastery_history(ctx.session_id, "E1") == [True, True]

    # 资源包：讲义（supported）+ 两道题归档 + 实操指南（procedure + procedure_guide）
    packages = store.load_packages(ctx.session_id)
    assert len(packages) == 1
    p = packages[0]
    assert [c["text"] for c in p["lecture"]] == ["论断一", "论断二"]
    assert [q["question_type"] for q in p["questions"]] == ["choice", "answer"]
    assert p["practice"] is not None and len(p["practice"]["steps"]) >= 1
    assert p["challenge"] is None  # 0.8 < 0.85
    assert p["difficulty_tier"] == "beginner"  # 难度1 ≤ beginner 上限2

    events = _events(store, ctx.session_id)
    assert "package_saved" in events and "topic_advance" in events

    asyncio.run(loop.end_session(ctx))
    finished = store.get_session(ctx.session_id)
    assert finished is not None and finished["status"] == "finished"
    assert "session_end" in _events(store, ctx.session_id)


def test_scaffold_state_machine(tmp_path):
    """回答题失败 → 脚手架（镜像干扰项）→ 答对回回答题，且不洗白降维计数。"""
    feedbacks = [
        FeedbackOutput(verdict="correct", evaluation="选择题对", missed_requirements=[]),
        FeedbackOutput(verdict="incorrect", evaluation="回答题错", missed_requirements=[]),
        FeedbackOutput(verdict="correct", evaluation="脚手架对", missed_requirements=[]),
    ]
    loop, store = _setup(tmp_path, feedbacks=feedbacks)
    ctx = asyncio.run(loop.start_session("u1", _profile()))

    asyncio.run(loop.teach_round(ctx, "E1"))
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    asyncio.run(loop.handle_answer(ctx, "E1", q1, q1.expected_label))  # choice 对 → retry

    asyncio.run(loop.teach_round(ctx, "E1"))
    q2 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q2.question_type == "answer"
    asyncio.run(loop.handle_answer(ctx, "E1", q2, "完全错误的回答"))  # answer 错 → retry

    # 下一轮：先脚手架
    progress = loop.progress(ctx.session_id, "E1")
    assert progress.scaffold_pending
    asyncio.run(loop.teach_round(ctx, "E1"))
    q3 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q3.question_id.endswith("_scaffold")
    assert "镜像错误理解" in q3.options[1]  # LLM 干扰项首项 = 错误理解镜像
    assert "scaffold_offered" in _events(store, ctx.session_id)

    # 脚手架答对：不写掌握度（仍 [T,F]），决策重算不受脚手架影响
    before = store.load_mastery_history(ctx.session_id, "E1")
    r3 = asyncio.run(loop.handle_answer(ctx, "E1", q3, "A"))
    assert r3.decision == "retry"
    assert store.load_mastery_history(ctx.session_id, "E1") == before

    # 脚手架答对后回到回答题（题型单向推进，不再回泛化 choice）
    q4 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q4.question_type == "answer" and not q4.question_id.endswith("_scaffold")


def test_scaffold_wrong_keeps_scaffold(tmp_path):
    """脚手架答错：计一次错且保持脚手架状态（识别都没过）。"""
    feedbacks = [
        FeedbackOutput(verdict="correct", evaluation="选择题对", missed_requirements=[]),
        FeedbackOutput(verdict="incorrect", evaluation="回答题错", missed_requirements=[]),
        FeedbackOutput(verdict="incorrect", evaluation="脚手架错", missed_requirements=[]),
    ]
    loop, store = _setup(tmp_path, feedbacks=feedbacks)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    asyncio.run(loop.handle_answer(ctx, "E1", q1, q1.expected_label))
    asyncio.run(loop.teach_round(ctx, "E1"))
    q2 = asyncio.run(loop.next_question(ctx, "E1"))
    asyncio.run(loop.handle_answer(ctx, "E1", q2, "错误回答"))
    asyncio.run(loop.teach_round(ctx, "E1"))
    q3 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q3.question_id.endswith("_scaffold")
    asyncio.run(loop.handle_answer(ctx, "E1", q3, "B"))  # 脚手架错
    # 计数历史 [T,F,F]：连续两错 → regress；脚手架错被计入
    assert store.load_mastery_history(ctx.session_id, "E1") == [True, False, False]
    progress = loop.progress(ctx.session_id, "E1")
    assert progress.scaffold_pending  # 保持脚手架


def test_regress_emits_event(tmp_path):
    feedbacks = [
        FeedbackOutput(verdict="incorrect", evaluation="错1", missed_requirements=[]),
        FeedbackOutput(verdict="incorrect", evaluation="错2", missed_requirements=[]),
    ]
    loop, store = _setup(tmp_path, feedbacks=feedbacks)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    asyncio.run(loop.handle_answer(ctx, "E1", q1, "X"))  # choice 错
    asyncio.run(loop.teach_round(ctx, "E1"))
    q2 = asyncio.run(loop.next_question(ctx, "E1"))
    r2 = asyncio.run(loop.handle_answer(ctx, "E1", q2, "Y"))  # 又错 → 连错2 → regress
    assert r2.decision == "regress"
    events = _events(store, ctx.session_id)
    assert "topic_regress" in events
