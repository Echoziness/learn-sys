"""会话编排服务（W1）：诊断 → 切片 → 逐主题教学循环，CLI 与 Web 共用。

四原语（驱动方各自组合，循环本身不锁死在库内）：
  start_session  画像 → 诊断 + 切片 + 落库 + 事件
  teach_round    教学子图执行（retrieve→generate→review）+ 论断/裁决事件
  next_question  题型阶梯 + 脚手架状态机（幂等：pending 轮复用）
  handle_answer  判分 → 决策 → 掌握度沉淀 → 资源包组装（advance 时）

进度从历史推导（D2）：reached_answer / scaffold_pending / retry 上下文
全部由 topic_rounds + mastery_snapshots 历史计算，不另立内存状态——
任何进程重启后从 DB 即可续跑（api 无内存会话态）。

事件协议见架构文档 §4；payload 自包含，前端仅凭 payload 可渲染。
expected 判分要点永不进事件（学生视野隔离），只落 topic_rounds.expected_json。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.agents.diagnose import diagnose_node
from core.agents.question import build_scaffold_distractors, question_node
from core.answer_pipeline import AnswerOutcome, process_answer
from core.assess import Question, build_question
from core.config import Settings
from core.deliver import (
    archive_questions,
    build_challenge,
    build_lecture,
    difficulty_tier_for,
    extract_practice,
)
from core.llm import LLMProvider
from core.mastery import compute_mastery, decide_next_step
from core.plan import KnowledgeEntry, Plan, build_plan
from core.session import SessionStore
from core.state import AgentState, DraftClaim, LearnerProfile, ReviewNote

logger = structlog.get_logger()

# 重教轮的选择题答对提示：识别已通过，重教必须向应用深度推进
_CHOICE_PASS_HINT = (
    "上一轮为选择题且回答正确（识别已通过）——本次重教不得复读基础定义，"
    "请向应用深度推进：讲概念的实际应用场景、常见误解与易错点。"
)


@dataclass
class SessionContext:
    """一个会话的全部推导输入（从 DB 可完整重建）。"""

    session_id: str
    learner_id: str
    profile: LearnerProfile
    difficulty_level: str
    profile_summary: str
    gap_ids: list[str]
    plan: Plan
    entries: list[KnowledgeEntry]

    def entry(self, entry_id: str) -> KnowledgeEntry:
        return next(e for e in self.entries if e.id == entry_id)


@dataclass
class TopicProgress:
    """由教学轮历史推导的主题进度（D2：无内存状态）。"""

    rounds: list[dict[str, Any]] = field(default_factory=list)
    correctness: list[bool] = field(default_factory=list)

    @property
    def answered(self) -> list[dict[str, Any]]:
        return [r for r in self.rounds if r.get("answer") is not None]

    @property
    def last_round(self) -> dict[str, Any] | None:
        answered = self.answered
        return answered[-1] if answered else None

    @property
    def next_round_no(self) -> int:
        return max((r["round_no"] for r in self.rounds), default=0) + 1

    @property
    def reached_answer(self) -> bool:
        """题型单向推进：出现过已作答的回答题即锁定 answer 深度。"""
        return any(
            (r.get("question") or {}).get("question_type") == "answer" for r in self.answered
        )

    @property
    def scaffold_pending(self) -> bool:
        """回答题（或脚手架）失败 → 下一轮先出脚手架选择题。"""
        last = self.last_round
        if not last:
            return False
        q = last.get("question") or {}
        if q.get("question_type") != "answer" and not q.get("question_id", "").endswith("_scaffold"):
            return False
        grade = last.get("grade") or {}
        return not grade.get("is_correct", False)

    @property
    def failed_question(self) -> dict[str, str] | None:
        """镜像干扰项来源：最近一次回答题的题目与作答。"""
        for r in reversed(self.answered):
            q = r.get("question") or {}
            if q.get("question_type") == "answer" and r.get("answer"):
                return {"prompt": q["prompt"], "answer": r["answer"]}
        return None

    @property
    def retry_context(self) -> str:
        """错因回流：上一轮题目 + 作答 + 评估（choice 答对时附加深度推进提示）。"""
        last = self.last_round
        if not last or not last.get("answer"):
            return ""
        q = last.get("question") or {}
        grade = last.get("grade") or {}
        hint = (
            _CHOICE_PASS_HINT
            if q.get("question_type") == "choice" and grade.get("is_correct")
            else ""
        )
        return (
            f"{hint}\n题目：{q.get('prompt', '')}\n"
            f"学生作答：{last['answer']}\n"
            f"评估：{grade.get('evaluation', '')}"
        ).strip("\n")


@dataclass
class TeachResult:
    """一轮教学（子图执行）的产出。"""

    entry_id: str
    round_no: int
    is_retry: bool
    claims: list[DraftClaim]
    reviews: list[ReviewNote]


@dataclass
class RoundResult:
    """一次作答处理的完整结果。"""

    outcome: AnswerOutcome
    question: Question
    round_no: int
    is_scaffold: bool
    decision: str
    mastery: float


class TeachLoop:
    """编排原语集合。一个实例可服务多个会话（状态全在 DB）。"""

    def __init__(
        self,
        *,
        graph,
        provider: LLMProvider,
        store: SessionStore,
        settings: Settings,
        entries: list[KnowledgeEntry],
    ):
        self._graph = graph
        self._provider = provider
        self._store = store
        self._settings = settings
        self._entries = entries

    # ---------- 诊断与切片 ----------

    async def start_session(self, learner_id: str, profile: LearnerProfile) -> SessionContext:
        sid = self._store.create_session(learner_id, profile.model_dump())
        await self._store.emit(
            sid, "session_start", {"learner_id": learner_id, "profile": profile.model_dump()}
        )

        catalog = [{"id": e.id, "title": e.title} for e in self._entries]
        diag = await diagnose_node(
            {"learner_profile": profile, "test_results": []},
            provider=self._provider,
            model=self._settings.diagnose_model,
            entry_catalog=catalog,
        )
        gap_ids: list[str] = diag["gap_ids"]
        difficulty_level: str = diag["difficulty_level"]
        summary: str = diag["profile_summary"]

        plan = build_plan(self._entries, gap_ids or diag["gaps"], max_difficulty=5)
        self._store.save_diagnosis(
            sid,
            gap_ids=gap_ids,
            difficulty_level=difficulty_level,
            profile_summary=summary,
            plan=self._plan_dict(plan),
        )
        await self._store.emit(
            sid,
            "diagnose_done",
            {
                "gap_ids": gap_ids,
                "gaps": diag["gaps"],
                "difficulty_level": difficulty_level,
                "summary": summary,
            },
        )
        await self._store.emit(
            sid,
            "plan_done",
            {
                "topics": [
                    {
                        "entry_id": t.entry_id,
                        "title": t.title,
                        "order": t.order,
                        "target": t.target,
                    }
                    for t in plan.topics
                ],
                "uncovered_gaps": plan.uncovered_gaps,
            },
        )
        return SessionContext(
            session_id=sid,
            learner_id=learner_id,
            profile=profile,
            difficulty_level=difficulty_level,
            profile_summary=summary,
            gap_ids=gap_ids,
            plan=plan,
            entries=self._entries,
        )

    @staticmethod
    def _plan_dict(plan: Plan) -> dict[str, Any]:
        return {
            "topics": [dataclasses.asdict(t) for t in plan.topics],
            "uncovered_gaps": plan.uncovered_gaps,
        }

    # ---------- 教学子图 ----------

    async def teach_round(self, ctx: SessionContext, entry_id: str) -> TeachResult:
        entry = ctx.entry(entry_id)
        progress = self.progress(ctx.session_id, entry_id)
        # 重教作废未作答的轮（旧题基于旧教学内容，深度契约失效）
        self._store.delete_pending_rounds(ctx.session_id, entry_id)
        round_no = progress.next_round_no
        is_retry = progress.last_round is not None

        await self._store.emit(
            ctx.session_id,
            "topic_start",
            {"entry_id": entry_id, "title": entry.title, "round_no": round_no, "is_retry": is_retry},
        )

        state: AgentState = {
            "learner_id": ctx.learner_id,
            "gaps": [entry.title],
            "anchor_entry": entry,
            "difficulty_level": ctx.difficulty_level,
            "profile_summary": ctx.profile_summary,
            "review_round": 0,
        }
        if progress.retry_context:
            state["retry_context"] = progress.retry_context

        final: dict[str, Any] = {}
        async for update in self._graph.astream(state, stream_mode="updates"):
            for node, out in update.items():
                if not isinstance(out, dict):
                    continue
                final.update(out)
                await self._emit_node_event(ctx.session_id, entry_id, round_no, node, out)

        claims: list[DraftClaim] = final.get("draft", [])
        reviews: list[ReviewNote] = final.get("review_history", [])
        verdicts = {
            str(n.claim_index): n.verdict
            for n in reviews
        }
        await self._store.emit(
            ctx.session_id,
            "teach_delivered",
            {
                "entry_id": entry_id,
                "round_no": round_no,
                "claims": [
                    {
                        "claim_index": c.claim_index,
                        "text": c.text,
                        "evidence_ids": c.evidence_ids,
                        "claim_type": c.claim_type,
                    }
                    for c in claims
                ],
                "verdicts": verdicts,
            },
        )
        return TeachResult(
            entry_id=entry_id,
            round_no=round_no,
            is_retry=is_retry,
            claims=claims,
            reviews=reviews,
        )

    async def _emit_node_event(
        self, session_id: str, entry_id: str, round_no: int, node: str, out: dict[str, Any]
    ) -> None:
        """子图节点完成事件（裁判面的调度可视化数据）。"""
        if node == "retrieve":
            await self._store.emit(
                session_id,
                "retrieve_done",
                {
                    "entry_id": entry_id,
                    "round_no": round_no,
                    "entries": [
                        {"id": e.id, "title": e.title, "score": round(e.score, 4)}
                        for e in out.get("retrieved_entries", [])
                    ],
                    "uncovered": out.get("uncovered_gaps", []),
                },
            )
        elif node == "generate":
            cited = [e.id for e in out.get("cited_entries", [])]
            await self._store.emit(
                session_id,
                "generate_done",
                {
                    "entry_id": entry_id,
                    "round_no": round_no,
                    "claims_count": len(out.get("draft", [])),
                    "cited": cited,
                },
            )
        elif node == "review":
            notes = out.get("review_history", [])
            await self._store.emit(
                session_id,
                "review_done",
                {
                    "entry_id": entry_id,
                    "round_no": round_no,
                    "verdicts": [
                        {"claim_index": n.claim_index, "verdict": n.verdict, "reason": n.reason}
                        for n in notes
                    ],
                    "unsupported_count": sum(1 for n in notes if n.verdict == "unsupported"),
                    "review_round": out.get("review_round", 0),
                },
            )

    # ---------- 出题 ----------

    async def next_question(self, ctx: SessionContext, entry_id: str) -> Question:
        """幂等出题：pending 轮复用（web 刷新安全），否则按状态机构造新题。"""
        entry = ctx.entry(entry_id)

        pending = self._store.get_pending_round(ctx.session_id, entry_id)
        if pending is not None:
            return self._question_from_record(pending)

        progress = self.progress(ctx.session_id, entry_id)
        round_no = progress.next_round_no

        if progress.scaffold_pending and progress.failed_question:
            question = await self._build_scaffold(ctx, entry, progress.failed_question)
            await self._store.emit(
                ctx.session_id,
                "scaffold_offered",
                {"entry_id": entry_id, "round_no": round_no, "mirror": "上一轮作答的典型错误理解"},
            )
        else:
            correctness = self._store.load_mastery_history(ctx.session_id, entry_id)
            question = build_question(
                entry,
                distractors=self._entries,
                mastery=compute_mastery(correctness),
                floor_type="answer" if progress.reached_answer else None,
            )
            if question.question_type == "answer":
                question = await self._build_answer_question(ctx, entry, question, round_no)

        # question_id 编码轮次与题型：资源包按 id 去重时不同轮/题型不互相覆盖
        suffix = (
            "_scaffold"
            if question.question_id.endswith("_scaffold")
            else f"_{question.question_type}"
        )
        question = dataclasses.replace(
            question, question_id=f"q_{entry_id}_r{round_no}{suffix}"
        )

        self._store.save_round(
            ctx.session_id,
            entry_id=entry_id,
            round_no=round_no,
            question=self._question_record(question),
            expected=list(question.expected_keywords),
            answer=None,
            grade=None,
            decision="pending",
            mastery_after=None,
        )
        await self._store.emit(
            ctx.session_id,
            "question_built",
            {
                "entry_id": entry_id,
                "round_no": round_no,
                "question_id": question.question_id,
                "question_type": question.question_type,
                "prompt": question.prompt,
                "options": list(question.options),
            },
        )
        return question

    async def _build_answer_question(
        self, ctx: SessionContext, entry: KnowledgeEntry, fallback: Question, round_no: int
    ) -> Question:
        """回答题：LLM 生成场景化题干 + expected（深度契约：不超本轮教学内容）。"""
        taught = self._last_taught_claims(ctx.session_id, entry.id)
        try:
            q = await question_node(
                {
                    "entry": {
                        "id": entry.id,
                        "title": entry.title,
                        "content": entry.content,
                        "keywords": entry.keywords,
                    },
                    "taught_claims": taught,
                },
                provider=self._provider,
                model=self._settings.question_model,
            )
        except Exception:
            logger.warning("question_llm_failed_fallback_template", entry_id=entry.id)
            return fallback
        if not q["question"]:
            return fallback
        expected = tuple(q["expected_keywords"]) or fallback.expected_keywords
        return Question(
            question_id=f"q_{entry.id}_r{round_no}_answer",
            entry_id=entry.id,
            prompt=q["question"],
            question_type="answer",
            expected_keywords=expected,
        )

    async def _build_scaffold(
        self, ctx: SessionContext, entry: KnowledgeEntry, failed: dict[str, str]
    ) -> Question:
        """脚手架选择题：正确项 = 条目 keywords；干扰项首项镜像学生错误理解。"""
        correct_text = "、".join(entry.keywords[:6]) or entry.title
        dists: list[str] = []
        try:
            dists = await build_scaffold_distractors(
                self._provider,
                failed["prompt"],
                failed["answer"],
                correct_text,
                model=self._settings.question_model,
            )
        except Exception:
            logger.warning("scaffold_llm_failed_fallback_distractors", entry_id=entry.id)
        if not dists:
            for other in self._entries:
                text = "、".join(other.keywords[:6])
                if text and text != correct_text and text not in dists:
                    dists.append(text)
                    if len(dists) >= 3:
                        break
        labels = "ABCD"
        options = [f"A. {correct_text}", *[f"{labels[i]}. {t}" for i, t in enumerate(dists, 1)]]
        return Question(
            question_id=f"q_{entry.id}_scaffold",
            entry_id=entry.id,
            prompt=f"关于上一题（{failed['prompt']}），以下哪个选项是正确的做法？",
            question_type="choice",
            expected_keywords=(),
            options=tuple(options),
            expected_label="A",
        )

    def _last_taught_claims(self, session_id: str, entry_id: str) -> list[str]:
        """深度契约输入：该条目最近一次教学的论断全文（来自事件流，D2 可重建）。"""
        for event in reversed(self._store.load_events(session_id, limit=500)):
            if event.event_type == "teach_delivered" and event.payload.get("entry_id") == entry_id:
                return [c["text"] for c in event.payload.get("claims", [])]
        return []

    # ---------- 作答与决策 ----------

    async def handle_answer(
        self, ctx: SessionContext, entry_id: str, question: Question, answer: str
    ) -> RoundResult:
        correctness = self._store.load_mastery_history(ctx.session_id, entry_id)
        outcome = await process_answer(
            self._provider,
            question,
            answer,
            correctness,
            model=self._settings.feedback_model,
        )
        is_scaffold = question.question_id.endswith("_scaffold")
        rounds = self._store.load_rounds(ctx.session_id, entry_id)
        round_no = max((r["round_no"] for r in rounds), default=1)

        # 脚手架答对不计入掌握度历史 → 决策也必须从"计数历史"推导
        # （防脚手架洗白连续错降维计数、防虚高 mastery 触发 advance）
        if is_scaffold and outcome.is_correct:
            decision, mastery = decide_next_step(correctness)
        else:
            decision, mastery = outcome.decision, outcome.mastery
            self._store.save_mastery(
                ctx.session_id,
                ctx.learner_id,
                entry_id,
                round_no=round_no,
                correctness=outcome.is_correct,
                mastery_after=mastery,
            )

        grade = {
            "is_correct": outcome.is_correct,
            "verdict": "correct" if outcome.is_correct else "partial",
            "coverage": round(outcome.grade.keyword_coverage, 3),
            "evaluation": outcome.evaluation,
            "llm_reviewed": outcome.llm_reviewed,
            "missed_requirements": list(outcome.missed_requirements),
        }
        self._store.update_round_answer(
            ctx.session_id,
            entry_id,
            round_no,
            answer=answer,
            grade=grade,
            decision=decision,
            mastery_after=mastery,
        )
        await self._store.emit(
            ctx.session_id,
            "answer_graded",
            {
                "entry_id": entry_id,
                "round_no": round_no,
                "question_id": question.question_id,
                "is_scaffold": is_scaffold,
                "is_correct": outcome.is_correct,
                "coverage": grade["coverage"],
                "evaluation": outcome.evaluation,
                "missed_requirements": list(outcome.missed_requirements),
                "decision": decision,
                "mastery_after": round(mastery, 3),
            },
        )

        entry = ctx.entry(entry_id)
        if decision == "advance":
            await self._deliver_package(ctx, entry, mastery)
            await self._store.emit(
                ctx.session_id,
                "topic_advance",
                {"entry_id": entry_id, "mastery": round(mastery, 3), "reached_gate": mastery >= 0.7},
            )
        elif decision == "regress":
            prereq = entry.prerequisites[0] if entry.prerequisites else None
            await self._store.emit(
                ctx.session_id,
                "topic_regress",
                {"entry_id": entry_id, "prereq_id": prereq, "reason": "连续答错，地基未打牢"},
            )
        return RoundResult(
            outcome=outcome,
            question=question,
            round_no=round_no,
            is_scaffold=is_scaffold,
            decision=decision,
            mastery=mastery,
        )

    async def _deliver_package(self, ctx: SessionContext, entry: KnowledgeEntry, mastery: float) -> None:
        """资源沉淀（PRD FR-11~15）：三形态 + 溯源 + 进阶标记。"""
        claims, reviews = self._last_teach_with_verdicts(ctx.session_id, entry.id)
        rounds = self._store.load_rounds(ctx.session_id, entry.id)
        practice = extract_practice(claims, reviews, knowledge_type=entry.knowledge_type)
        challenge = build_challenge(entry.title, mastery=mastery)
        tier = difficulty_tier_for(ctx.difficulty_level, entry.difficulty)
        self._store.upsert_package(
            ctx.session_id,
            ctx.learner_id,
            entry.id,
            lecture=build_lecture(claims, reviews, round_no=max((r["round_no"] for r in rounds), default=1)),
            questions=archive_questions(rounds),
            practice=practice,
            challenge=challenge,
            difficulty_tier=tier,
        )
        await self._store.emit(
            ctx.session_id,
            "package_saved",
            {
                "entry_id": entry.id,
                "lecture_count": len(claims),
                "question_count": len(rounds),
                "has_practice": practice is not None,
                "has_challenge": challenge is not None,
                "difficulty_tier": tier,
            },
        )

    def _last_teach_with_verdicts(
        self, session_id: str, entry_id: str
    ) -> tuple[list[DraftClaim], list[ReviewNote]]:
        """从最近一次 teach_delivered 事件重建 claims + 裁决（讲义组装输入）。"""
        for event in reversed(self._store.load_events(session_id, limit=500)):
            if event.event_type == "teach_delivered" and event.payload.get("entry_id") == entry_id:
                claims = [
                    DraftClaim(
                        claim_index=c["claim_index"],
                        text=c["text"],
                        evidence_ids=c["evidence_ids"],
                        claim_type=c.get("claim_type", "core"),
                    )
                    for c in event.payload.get("claims", [])
                ]
                notes = [
                    ReviewNote(
                        claim_index=int(idx),
                        verdict=verdict,  # type: ignore[arg-type]
                        reason="来自 teach_delivered 事件",
                    )
                    for idx, verdict in event.payload.get("verdicts", {}).items()
                ]
                return claims, notes
        return [], []

    # ---------- 进度推导 ----------

    def progress(self, session_id: str, entry_id: str) -> TopicProgress:
        """主题进度 = 教学轮历史 + 掌握度历史的纯推导（D2）。"""
        return TopicProgress(
            rounds=self._store.load_rounds(session_id, entry_id),
            correctness=self._store.load_mastery_history(session_id, entry_id),
        )

    # ---------- 序列化辅助 ----------

    @staticmethod
    def _question_record(question: Question) -> dict[str, Any]:
        return {
            "question_id": question.question_id,
            "entry_id": question.entry_id,
            "question_type": question.question_type,
            "prompt": question.prompt,
            "options": list(question.options),
            "expected_label": question.expected_label,
        }

    @staticmethod
    def _question_from_record(record: dict[str, Any]) -> Question:
        q = record["question"]
        return Question(
            question_id=q["question_id"],
            entry_id=q.get("entry_id", record["entry_id"]),
            prompt=q["prompt"],
            question_type=q["question_type"],
            expected_keywords=tuple(record.get("expected") or ()),
            options=tuple(q.get("options", ())),
            expected_label=q.get("expected_label", ""),
        )

    async def end_session(self, ctx: SessionContext, *, status: str = "finished") -> None:
        packages = self._store.load_packages(ctx.session_id)
        await self._store.emit(
            ctx.session_id,
            "session_end",
            {
                "topics_taught": len(packages),
                "packages": [p["entry_id"] for p in packages],
            },
        )
        self._store.finish_session(ctx.session_id, status=status)


__all__ = ["RoundResult", "SessionContext", "TeachLoop", "TeachResult", "TopicProgress"]
