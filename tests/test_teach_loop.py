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
from core.agents.question import ChoiceQuestionOutput, QuestionOutput, ScaffoldOutput
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
        self.prompts: list[str] = []  # 记录 prompt 文本（上下文契约断言用）
        self._choice_calls = 0

    async def chat_validated(self, messages, schema, model=None, **kwargs):
        name = schema.__name__
        self.calls.append(name)
        self.prompts.append(str(messages[0].get("content", "")))
        if name == "DiagnoseOutput":
            return DiagnoseOutput(
                gaps=["E1"], gap_ids=["E1"], profile_summary="测试摘要", difficulty_level="beginner"
            )
        if name == "QuestionOutput":
            return QuestionOutput(
                question="结合场景说明 E1 的要点？", expected_keywords=["甲", "乙"]
            )
        if name == "ScaffoldOutput":
            return ScaffoldOutput(
                question="关于 E1，下面哪个理解是正确的？",
                correct="甲是 E1 的核心概念，乙是配套",
                distractors=["镜像错误理解", "无关干扰项"],
            )
        if name == "ChoiceQuestionOutput":
            # 每次生成不同题干（测"已作答后重新生成"时断言两题不同）
            self._choice_calls += 1
            n = self._choice_calls
            return ChoiceQuestionOutput(
                question=f"关于主题一的理解（第{n}次出题），下列说法正确的是？",
                correct="论断一描述的内容是本主题的核心",
                distractors=[
                    "论断一描述的内容与本主题无关",
                    "主题一的核心内容在考试中不要求掌握",
                    "论断一在本主题中不起任何作用",
                ],
            )
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

    # R2：识别通过推进教学（advance_hint 通道，不再伪装成错因）→ 回答题（mastery 0.5 ≥ 0.5）
    teach2 = asyncio.run(loop.teach_round(ctx, "E1"))
    assert teach2.round_no == 2 and teach2.is_retry
    graph = loop._graph
    assert isinstance(graph, FakeGraph)
    r2_state = graph.states[-1]
    assert "题目" not in (r2_state.get("retry_context") or "")  # 答对无错因回流
    assert "识别已通过" in r2_state.get("advance_hint", "")  # 推进提示注入
    assert r2_state.get("taught_previously") == ["论断一", "论断二"]  # 去重输入
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
    """回答题失败 → 脚手架（镜像干扰项）→ 答对回回答题，且不洗白降维计数。

    注：choice 答对不调 LLM——feedbacks 队列从 R2 回答题开始消费。
    """
    feedbacks = [
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
    """脚手架答错：计一次错且保持脚手架状态（识别都没过）。

    注：choice 答对不调 LLM——feedbacks 队列从 R2 回答题开始消费。
    """
    feedbacks = [
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


# ── 上下文工程修复（2026-08-15）：进度推导信号 + 缓存 + 注入 ────────────


def test_progress_retry_signal(tmp_path):
    """答错 + LLM 给出遗漏清单 → 降维信号（遗漏 + 连错计数）；答对 → 无信号。"""
    loop, store = _setup(tmp_path)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    sid = ctx.session_id
    q = {"question_id": "q_E1_r1_answer", "entry_id": "E1", "question_type": "answer",
         "prompt": "第一题", "options": [], "expected_label": ""}
    store.save_round(sid, entry_id="E1", round_no=1, question=q, expected=["甲"],
                     answer="某作答",
                     grade={"is_correct": False, "missed_requirements": ["未提及甲"]},
                     decision="retry", mastery_after=0.3)
    store.save_mastery(sid, "u1", "E1", round_no=1, correctness=False, mastery_after=0.3)
    sig = loop.progress(sid, "E1").retry_signal
    assert sig == {"missed_requirements": ["未提及甲"], "recent_wrong_count": 1}

    # 答对轮：无信号
    q2 = {**q, "question_id": "q_E1_r2_answer", "prompt": "第二题"}
    store.save_round(sid, entry_id="E1", round_no=2, question=q2, expected=["甲"],
                     answer="甲", grade={"is_correct": True, "missed_requirements": []},
                     decision="retry", mastery_after=0.6)
    assert loop.progress(sid, "E1").retry_signal is None


def test_progress_retry_signal_counts_consecutive_wrong(tmp_path):
    loop, store = _setup(tmp_path)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    sid = ctx.session_id
    for i, correct in enumerate([True, False, False], start=1):
        store.save_round(sid, entry_id="E1", round_no=i,
                         question={"question_id": f"q{i}", "entry_id": "E1",
                                   "question_type": "answer", "prompt": f"题{i}",
                                   "options": [], "expected_label": ""},
                         expected=["甲"], answer="作答",
                         grade={"is_correct": correct, "missed_requirements": ["遗漏"]},
                         decision="retry", mastery_after=0.3)
        store.save_mastery(sid, "u1", "E1", round_no=i, correctness=correct, mastery_after=0.3)
    sig = loop.progress(sid, "E1").retry_signal
    assert sig is not None
    assert sig["recent_wrong_count"] == 2


def test_previous_questions_collected(tmp_path):
    loop, store = _setup(tmp_path)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    sid = ctx.session_id
    for i, prompt in enumerate(["第一题", "第二题", "第三题"], start=1):
        store.save_round(sid, entry_id="E1", round_no=i,
                         question={"question_id": f"q{i}", "entry_id": "E1",
                                   "question_type": "answer", "prompt": prompt,
                                   "options": [], "expected_label": ""},
                         expected=[], answer=None, grade=None,
                         decision="pending", mastery_after=None)
    assert loop.progress(sid, "E1").previous_questions == ["第一题", "第二题", "第三题"]


def test_choice_question_cached_and_regenerated(tmp_path):
    """choice 概念题缓存语义：未作答走缓存（幂等刷新）；已作答后重新生成。

    旧语义（干扰项按 entry 缓存，原题重考）的问题：答错重教后再考原题
    测不出新理解。新语义下已作答的 choice 一律换题。
    """
    feedbacks = [
        FeedbackOutput(verdict="incorrect", evaluation="choice 错", missed_requirements=[]),
        FeedbackOutput(verdict="incorrect", evaluation="choice 又错", missed_requirements=[]),
    ]
    loop, store = _setup(tmp_path, feedbacks=feedbacks)
    provider = loop._provider
    assert isinstance(provider, FakeProvider)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q1.question_type == "choice"
    # 概念辨析题：不再是"哪组要点属于X"关键词归属题
    assert "哪组要点" not in q1.prompt
    # 正确项随机落位，但 expected_label 指向的选项必须是判分认定的正确项
    assert q1.expected_label in "ABCD"
    assert provider.calls.count("ChoiceQuestionOutput") == 1

    asyncio.run(loop.next_question(ctx, "E1"))  # pending 轮幂等复用，不追加 LLM 调用
    assert provider.calls.count("ChoiceQuestionOutput") == 1

    asyncio.run(loop.handle_answer(ctx, "E1", q1, "Z"))  # 错 → mastery 0
    asyncio.run(loop.teach_round(ctx, "E1"))  # 重教
    q2 = asyncio.run(loop.next_question(ctx, "E1"))
    assert q2.question_type == "choice"
    assert provider.calls.count("ChoiceQuestionOutput") == 2  # 已作答 → 重新生成
    assert q2.prompt != q1.prompt  # 不原题重考


def test_retry_round_injects_taught_previously(tmp_path):
    """重教轮：此前已教论断注入教学子图 state（去重输入）。"""
    feedbacks = [FeedbackOutput(verdict="correct", evaluation="choice 对", missed_requirements=[])]
    graph = FakeGraph()
    loop, store = _setup(tmp_path, feedbacks=feedbacks)
    loop._graph = graph  # type: ignore[assignment]
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    asyncio.run(loop.teach_round(ctx, "E1"))
    q1 = asyncio.run(loop.next_question(ctx, "E1"))
    asyncio.run(loop.handle_answer(ctx, "E1", q1, q1.expected_label))  # retry

    asyncio.run(loop.teach_round(ctx, "E1"))  # 重教轮
    state = graph.states[-1]
    assert state.get("taught_previously") == ["论断一", "论断二"]


# ── 巩固模式（2026-08-15）：answer 答对未达门槛 → 跳过教学直接确认 ──────


def test_needs_teaching_consolidation_mode(tmp_path):
    """answer 答对 → needs_teaching=False（直接出题）；choice 答对/答错 → True。"""
    loop, store = _setup(tmp_path)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    sid = ctx.session_id

    def _save(i: int, qtype: str, correct: bool, scaffold: bool = False) -> None:
        qid = f"q_E1_r{i}_{'choice_scaffold' if scaffold else qtype}"
        store.save_round(
            sid, entry_id="E1", round_no=i,
            question={"question_id": qid, "entry_id": "E1", "question_type": qtype,
                      "prompt": f"题{i}", "options": [], "expected_label": "A"},
            expected=["甲"], answer="作答",
            grade={"is_correct": correct, "missed_requirements": []},
            decision="retry", mastery_after=0.5,
        )
        store.save_mastery(sid, "u1", "E1", round_no=i, correctness=correct, mastery_after=0.5)

    assert loop.progress(sid, "E1").needs_teaching is True  # 首轮
    _save(1, "choice", True)
    assert loop.progress(sid, "E1").needs_teaching is True  # choice 答对：应用推进教学
    _save(2, "answer", True)
    assert loop.progress(sid, "E1").needs_teaching is False  # answer 答对：巩固确认
    _save(3, "answer", False)
    assert loop.progress(sid, "E1").needs_teaching is True  # answer 答错：错因重教
    _save(4, "choice", True, scaffold=True)
    assert loop.progress(sid, "E1").needs_teaching is True  # 脚手架答对：仍教学巩固


def test_retry_context_only_when_wrong(tmp_path):
    """choice 答对不再触发错因回流（extension 语义污染修复）。"""
    loop, store = _setup(tmp_path)
    ctx = asyncio.run(loop.start_session("u1", _profile()))
    sid = ctx.session_id
    store.save_round(
        sid, entry_id="E1", round_no=1,
        question={"question_id": "q_E1_r1_choice", "entry_id": "E1",
                  "question_type": "choice", "prompt": "题1", "options": [], "expected_label": "A"},
        expected=[], answer="A",
        grade={"is_correct": True, "evaluation": "对"},
        decision="retry", mastery_after=0.5,
    )
    progress = loop.progress(sid, "E1")
    assert progress.retry_context == ""  # 答对无错因回流
    assert progress.choice_passed is True  # 走推进提示通道
