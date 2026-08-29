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
import random
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.agents.diagnose import diagnose_node
from core.agents.question import (
    ChoiceQuestionOutput,
    QuestionInput,
    choice_node,
    followup_node,
    question_node,
    scaffold_node,
)
from core.agents.review import latest_verdicts
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
from core.plan import KnowledgeEntry, Plan, PlanTopic, build_plan
from core.session import SessionStore
from core.state import AgentState, DraftClaim, LearnerProfile, ReviewNote

logger = structlog.get_logger()

# 识别通过后的教学推进提示：choice 答对 = 识别层通过，教学向应用深度推进。
# 与错因回流（retry_context）分通道——识别通过不是错因，不得触发 extension 论断。
# 推进必须仍在条目概念范围内（条目概念的深化运用/与已学概念的联系）——
# "常见误解清单"类内容条目里没有，会过不了审核（unsupported 超标实测教训）。
_ADVANCE_HINT = (
    "上一轮为选择题且回答正确（识别已通过）——本次教学不得复读基础定义，"
    "请在条目概念范围内向应用深度推进：条目概念在真实场景中的作用、"
    "与本次检索条目中其他概念的联系、从条目内容可直接推导出的注意点。"
    "禁止引入条目之外的知识（如条目未记载的误解清单、工具选型建议）。"
    "本轮论断仍为 core 类型（这是新课推进，不是错因纠正）。"
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
    domain: str = "bigdata-analysis"  # 教学领域（seeds 子目录；检索过滤与上下文重建依据）

    def entry(self, entry_id: str) -> KnowledgeEntry:
        return next(e for e in self.entries if e.id == entry_id)


@dataclass
class TopicProgress:
    """由教学轮历史推导的主题进度（D2：无内存状态）。"""

    rounds: list[dict[str, Any]] = field(default_factory=list)
    correctness: list[bool] = field(default_factory=list)

    @property
    def main_rounds(self) -> list[dict[str, Any]]:
        """主教学轮（排除追问侧车轮）——题型阶梯/脚手架/错因等全部推导基于此。

        追问轮（question_id 以 _followup 结尾）是澄清工具侧车：不推进题型、
        不影响错因/降维信号；但占用 round_no 号段（next_round_no 含它）。
        """
        return [
            r
            for r in self.rounds
            if not str((r.get("question") or {}).get("question_id", "")).endswith("_followup")
        ]

    @property
    def answered(self) -> list[dict[str, Any]]:
        return [r for r in self.main_rounds if r.get("answer") is not None]

    @property
    def last_round(self) -> dict[str, Any] | None:
        answered = self.answered
        return answered[-1] if answered else None

    @property
    def needs_teaching(self) -> bool:
        """巩固模式（2026-08-15）：answer 答对但未达门槛 → 跳过教学直接出确认题。

        确定性规则，读历史不猜心：answer 答对 = 无明确教学锚点（矛盾检测
        保证 correct 蕴含无遗漏），唯一缺口是证据数量——再测一个侧面即可。
        mastery 数学不动：证据照常由作答累积，题型阶梯照常推进。
        choice 答对仍需教学（识别→回忆之间有真实教学空间：应用推进）。
        """
        last = self.last_round
        if not last:
            return True  # 首轮
        q = last.get("question") or {}
        grade = last.get("grade") or {}
        if q.get("question_type") != "answer":
            return True  # choice/脚手架：识别层，答对答错都有教学空间
        return not grade.get("is_correct", False)

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
        """错因回流：上一轮**答错**时的题目 + 作答 + 评估（extension 论断的触发源）。

        choice 答对不再走此通道（会污染 extension 语义——识别通过不是错因），
        推进提示见 choice_passed。
        """
        last = self.last_round
        if not last or not last.get("answer"):
            return ""
        grade = last.get("grade") or {}
        if grade.get("is_correct"):
            return ""
        q = last.get("question") or {}
        return (
            f"题目：{q.get('prompt', '')}\n"
            f"学生作答：{last['answer']}\n"
            f"评估：{grade.get('evaluation', '')}"
        ).strip("\n")

    @property
    def choice_passed(self) -> bool:
        """识别通过信号：上一轮 choice（含脚手架）答对 → 教学向应用深度推进。"""
        last = self.last_round
        if not last:
            return False
        q = last.get("question") or {}
        grade = last.get("grade") or {}
        return q.get("question_type") == "choice" and bool(grade.get("is_correct"))

    @property
    def retry_signal(self) -> dict[str, Any] | None:
        """失败降维信号：最近一轮答错时的遗漏清单 + 连续错次数。

        出题降维契约的输入——学生刚失败时，下一题必须聚焦遗漏要点降维，
        而非跟随最新教学轮的深度升维（死亡螺旋根因）。
        """
        last = self.last_round
        if not last or not last.get("answer"):
            return None
        grade = last.get("grade") or {}
        if grade.get("is_correct"):
            return None
        wrong = 0
        for c in reversed(self.correctness):
            if c:
                break
            wrong += 1
        missed = [m for m in grade.get("missed_requirements") or [] if m]
        if not missed:
            return None
        return {"missed_requirements": missed, "recent_wrong_count": wrong}

    @property
    def previous_questions(self) -> list[str]:
        """已出过的题干（防换皮重考，最近 5 条）。"""
        return [q["prompt"] for r in self.rounds[-5:] if (q := r.get("question"))]


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


@dataclass
class FollowupResult:
    """一次追问处理的结果：无效则只有判定理由，有效则携带困惑解答。"""

    valid: bool
    reason: str
    round_no: int
    answer: str | None = None


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
        entries_by_domain: dict[str, list[KnowledgeEntry]] | None = None,
    ):
        self._graph = graph
        self._provider = provider
        self._store = store
        self._settings = settings
        # 单域列表（默认域/单域部署）与按域索引（多域选择）并存：
        # 未传 entries_by_domain 时回退单域模式（CLI/评测/测试不变）
        self._entries = entries
        self._entries_by_domain = entries_by_domain or {"bigdata-analysis": entries}
        self._choice_q_cache: dict[str, ChoiceQuestionOutput | None] = {}  # choice 题按 entry_id 缓存

    # ---------- 诊断与切片 ----------

    async def start_session(
        self, learner_id: str, profile: LearnerProfile, *, domain: str = "bigdata-analysis"
    ) -> SessionContext:
        entries = self._entries_for(domain)
        sid = self._store.create_session(learner_id, profile.model_dump(), domain=domain)
        await self._store.emit(
            sid,
            "session_start",
            {"learner_id": learner_id, "domain": domain, "profile": profile.model_dump()},
        )

        catalog = [{"id": e.id, "title": e.title} for e in entries]
        diag = await diagnose_node(
            {"learner_profile": profile, "test_results": []},
            provider=self._provider,
            model=self._settings.diagnose_model,
            entry_catalog=catalog,
        )
        gap_ids: list[str] = diag["gap_ids"]
        difficulty_level: str = diag["difficulty_level"]
        summary: str = diag["profile_summary"]

        plan = build_plan(entries, gap_ids or diag["gaps"], max_difficulty=5)
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
            entries=entries,
            domain=domain,
        )

    def _entries_for(self, domain: str) -> list[KnowledgeEntry]:
        """按域取条目列表：多域选择模式下域不存在即报错（诊断目录/切片不能静默空集）。"""
        entries = self._entries_by_domain.get(domain)
        if not entries:
            raise KeyError(f"未知教学领域：{domain}")
        return entries

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
            "domain": ctx.domain,
        }
        if progress.retry_context:
            state["retry_context"] = progress.retry_context
        pending_followups = self.pending_followups(ctx.session_id, entry_id)
        if pending_followups:
            # 困惑回流（与错因回流同通道）：上次教学后学生主动提出的疑问，
            # 本轮必须针对性讲解——困惑是比错因更直接的教学锚点
            state["followup_context"] = "\n\n".join(
                f"学生疑问：{(r.get('question') or {}).get('prompt', '')}\n"
                f"当时已给出的解答：{r.get('answer', '')}"
                for r in pending_followups
            )
        if progress.choice_passed:
            state["advance_hint"] = _ADVANCE_HINT
        if is_retry:
            # 重教去重：此前各轮已教论断注入——禁止复读，重教必须给增量
            state["taught_previously"] = self._taught_previously(ctx.session_id, entry_id)

        final: dict[str, Any] = {}
        review_log: list[ReviewNote] = []  # append-only 裁决日志累积（供事件与最终裁决）
        async for update in self._graph.astream(state, stream_mode="updates"):
            for node, out in update.items():
                if not isinstance(out, dict):
                    continue
                final.update(out)
                review_log.extend(out.get("review_history", []))
                await self._emit_node_event(ctx.session_id, entry_id, round_no, node, out, review_log)

        claims: list[DraftClaim] = final.get("draft", [])
        # 当前裁决按论断取日志最新一条（旧轮被驳回的论断已由改写版裁决覆盖）
        current = latest_verdicts(review_log)
        verdicts = {
            str(c.claim_index): current[c.claim_index].verdict
            for c in claims
            if c.claim_index in current
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
            reviews=[current[c.claim_index] for c in claims if c.claim_index in current],
        )

    async def _emit_node_event(
        self,
        session_id: str,
        entry_id: str,
        round_no: int,
        node: str,
        out: dict[str, Any],
        review_log: list[ReviewNote] | None = None,
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
            # 裁决日志按论断取最新一条——事件展示当前全稿裁决（含上轮未复审的论断）
            notes = sorted(latest_verdicts(review_log or []).values(), key=lambda n: n.claim_index)
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
            return self.question_from_record(pending)

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
                question = await self._build_answer_question(ctx, entry, question, round_no, progress)
            else:
                question = await self._build_llm_choice(ctx, entry, question, progress)

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
        self,
        ctx: SessionContext,
        entry: KnowledgeEntry,
        fallback: Question,
        round_no: int,
        progress: TopicProgress,
    ) -> Question:
        """回答题：LLM 生成场景化题干 + expected。

        上下文契约：taught_claims（深度上限，带 claim_type 分层）、
        retry 信号（失败降维）、difficulty_level、previous_questions（防重考）。
        """
        taught = self._all_taught_claims(ctx.session_id, entry.id)
        q_state: QuestionInput = {
            "entry": {
                "id": entry.id,
                "title": entry.title,
                "content": entry.content,
                "keywords": entry.keywords,
            },
            "taught_claims": taught,
            "difficulty_level": ctx.difficulty_level,
            "previous_questions": progress.previous_questions,
        }
        retry = progress.retry_signal
        if retry is not None:
            q_state["retry"] = retry
        try:
            q = await question_node(
                q_state,
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

    async def _build_llm_choice(
        self,
        ctx: SessionContext,
        entry: KnowledgeEntry,
        fallback: Question,
        progress: TopicProgress,
    ) -> Question:
        """choice 题概念化：LLM 生成概念辨析题（题干 + 陈述句正确项 + 误解干扰项）。

        - 缓存按 entry_id（同会话幂等）；该条目已有**已作答**的 choice 时缓存失效
          ——答错重教后不得原题重考；
        - LLM 失败/校验不过回退确定性构造（fail-closed）；
        - 判分只认选项标签（assess 不变），正确项位置随机化（测评题防位置惯性）。
        """
        rounds = self._store.load_rounds(ctx.session_id, entry.id)
        answered_choice = any(
            r.get("question", {}).get("question_type") == "choice" and r.get("answer") is not None
            for r in rounds
        )
        # 未作答过的 choice 走缓存（幂等刷新）；已作答（无论对错）一律重新生成
        # ——原题重考测不出新理解，重教后必须换题
        cached: ChoiceQuestionOutput | None = (
            self._choice_q_cache.get(entry.id) if not answered_choice else None
        )
        if cached is None:
            try:
                cached = await choice_node(
                    {
                        "entry": {
                            "id": entry.id,
                            "title": entry.title,
                            "content": entry.content,
                            "keywords": entry.keywords,
                        },
                        "taught_claims": self._all_taught_claims(ctx.session_id, entry.id),
                        "difficulty_level": ctx.difficulty_level,
                        "previous_questions": [
                            q
                            for q in progress.previous_questions
                            if "哪组要点" not in q  # 旧版关键词归属题不算重考素材
                        ],
                    },
                    provider=self._provider,
                    model=self._settings.question_model,
                )
            except Exception:
                logger.warning("choice_llm_failed_fallback_deterministic", entry_id=entry.id)
                cached = None
            self._choice_q_cache[entry.id] = cached
        if cached is None:
            return fallback

        texts = [cached.correct, *cached.distractors][:4]
        order = list(range(len(texts)))
        random.shuffle(order)
        labels = "ABCD"
        options = tuple(f"{labels[i]}. {texts[order[i]]}" for i in range(len(texts)))
        expected_label = labels[order.index(0)]
        return dataclasses.replace(
            fallback,
            prompt=cached.question,
            options=options,
            expected_label=expected_label,
        )

    async def _build_scaffold(
        self, ctx: SessionContext, entry: KnowledgeEntry, failed: dict[str, str]
    ) -> Question:
        """脚手架选择题：LLM 一次生成题干 + 正确项（教学论断提炼的陈述句）+ 干扰项。

        正确项固定 A 位（教学对比工具，非测评，不需要位置随机化）。
        LLM 失败/校验不过回退确定性构造（题干与关键词堆语义对齐）。
        """
        claims = self._all_taught_claims(ctx.session_id, entry.id)
        entry_dict = {
            "id": entry.id,
            "title": entry.title,
            "content": entry.content,
            "keywords": entry.keywords,
        }
        try:
            s = await scaffold_node(
                {
                    "entry": entry_dict,
                    "taught_claims": claims,
                    "failed_question": failed["prompt"],
                    "student_answer": failed["answer"],
                },
                provider=self._provider,
                model=self._settings.question_model,
            )
        except Exception:
            logger.warning("scaffold_llm_failed_fallback_deterministic", entry_id=entry.id)
            s = None
        if s is not None:
            options = tuple(
                f"{label}. {text}"
                for label, text in zip("ABCD", [s.correct, *s.distractors], strict=False)
            )
            return Question(
                question_id=f"q_{entry.id}_scaffold",
                entry_id=entry.id,
                prompt=s.question,
                question_type="choice",
                expected_keywords=(),
                options=options,
                expected_label="A",
            )
        # 确定性回退：题干与关键词堆选项语义对齐（不再错位问"正确的做法"）
        correct_text = "、".join(entry.keywords[:6]) or entry.title
        dists: list[str] = []
        for other in self._entries:
            text = "、".join(other.keywords[:6])
            if text and text != correct_text and text not in dists:
                dists.append(text)
                if len(dists) >= 3:
                    break
        labels = "ABCD"
        options = (f"A. {correct_text}", *[f"{labels[i]}. {t}" for i, t in enumerate(dists, 1)])
        return Question(
            question_id=f"q_{entry.id}_scaffold",
            entry_id=entry.id,
            prompt=f"以下哪组概念属于「{entry.title}」的核心内容？",
            question_type="choice",
            expected_keywords=(),
            options=options,
            expected_label="A",
        )

    # ---------- 动态追问（记录困惑 → 解答 → 下轮注入，2026-08-28 设计回归） ----------

    def _current_question(self, session_id: str, entry_id: str) -> dict[str, Any]:
        """当前题目上下文（追问注入用）：最近主教学轮的题目 + 学生作答 + 判定。

        学生追问通常发生在面对当前题目时——注入本题让 followup_node 能回答
        针对题干/选项/作答的疑问。不注入 expected/expected_label（防泄题）。
        """
        for r in reversed(self._store.load_rounds(session_id, entry_id)):
            q = r.get("question")
            if q and not str(r.get("decision", "")).endswith("followup"):
                result: dict[str, Any] = {
                    "question_id": q.get("question_id", ""),
                    "question_type": q.get("question_type", ""),
                    "prompt": q.get("prompt", ""),
                    "options": q.get("options", []),
                }
                if r.get("answer") is not None:
                    result["student_answer"] = r["answer"]
                    grade = r.get("grade") or {}
                    result["is_correct"] = grade.get("is_correct", False)
                return result
        return {}

    def _last_taught_round_no(self, session_id: str, entry_id: str) -> int:
        """该条目最近一次完成教学（teach_delivered）的轮次号（未教过返回 0）。"""
        last = 0
        for event in self._store.load_events(session_id, limit=500):
            if event.event_type == "teach_delivered" and event.payload.get("entry_id") == entry_id:
                rn = event.payload.get("round_no")
                if rn is not None:
                    last = max(last, int(rn))
        return last

    def pending_followups(self, session_id: str, entry_id: str) -> list[dict[str, Any]]:
        """未消化的困惑记录：最近一次教学轮之后落库的有效追问。

        消化状态从历史推导（进度从历史推导原则）：困惑记录轮号 > 最近教学轮号
        = 尚未被针对性教学回应 → 下一轮教学注入。教学完成后自然消化，无状态标记。
        """
        last_taught = self._last_taught_round_no(session_id, entry_id)
        out: list[dict[str, Any]] = []
        for r in self._store.load_rounds(session_id, entry_id):
            if r.get("decision") != "followup" or r.get("answer") is None:
                continue
            if int(r["round_no"]) > last_taught:
                out.append(r)
        return out

    def last_followup_record(self, session_id: str, entry_id: str) -> dict[str, Any] | None:
        """最近一条有效困惑记录（前端刷新恢复展示）：学生疑问 + 系统解答。"""
        for r in reversed(self._store.load_rounds(session_id, entry_id)):
            if r.get("decision") == "followup" and r.get("answer") is not None:
                q = r.get("question") or {}
                return {
                    "round_no": r["round_no"],
                    "question": q.get("prompt", ""),
                    "answer": r["answer"],
                }
        return None

    async def handle_followup(
        self, ctx: SessionContext, entry_id: str, question_text: str
    ) -> FollowupResult:
        """学生追问：LLM 判定有效性 → 有效则**记录困惑并直接给出解答**。

        设计回归（2026-08-28）：学生因困惑而提问，系统记录困惑并解答，不即时
        反问确认题。困惑记录落 topic_rounds 侧车轮（decision='followup'，
        answer 字段承载系统解答）：不推进题型、不进掌握度；由
        pending_followups 推导注入下一轮教学（错因回流同通道），并作为
        distill 误区提炼原料（与错题同管道）。
        """
        entry = ctx.entry(entry_id)
        progress = self.progress(ctx.session_id, entry_id)
        # 轮号必须严格大于最近已教轮（教学轮不落 topic_rounds，仅靠轮记录推导会把
        # 困惑排到已教轮之前 → 永远不会被注入）；也须避开主轮未作答题的占用号段，
        # 撞号时顺延（冲突概率低：追问发生在主轮作答后/教学完成后）
        round_no = max(progress.next_round_no, self._last_taught_round_no(ctx.session_id, entry_id) + 1)
        if any(r["round_no"] == round_no for r in self._store.load_rounds(ctx.session_id, entry_id)):
            round_no += 1

        # 当前题目注入：让学生对本题的疑问能被 followup_node 看见（题干+作答+判定）
        current_question = self._current_question(ctx.session_id, entry.id)
        judgement = await followup_node(
            {
                "entry": {
                    "id": entry.id,
                    "title": entry.title,
                    "content": entry.content,
                    "keywords": entry.keywords,
                },
                "taught_claims": self._all_taught_claims(ctx.session_id, entry.id),
                "student_question": question_text,
                "current_question": current_question,
            },
            provider=self._provider,
            model=self._settings.question_model,
        )
        await self._store.emit(
            ctx.session_id,
            "followup_asked",
            {
                "entry_id": entry_id,
                "round_no": round_no,
                "student_question": question_text,
                "valid": judgement.valid,
                "reason": judgement.reason,
                "answer": judgement.answer or "",
            },
        )
        if not judgement.valid or judgement.answer is None:
            return FollowupResult(valid=False, reason=judgement.reason, round_no=round_no)

        self._store.save_round(
            ctx.session_id,
            entry_id=entry_id,
            round_no=round_no,
            question={
                "question_id": f"q_{entry.id}_r{round_no}_followup",
                "entry_id": entry.id,
                "question_type": "followup",  # 困惑记录标识（非题目）
                "prompt": question_text,  # 学生疑问原文 = 困惑记录本体
                "options": [],
                "expected_label": "",
            },
            expected=[],
            answer=judgement.answer,  # 系统解答（本轮无学生作答）
            grade=None,
            decision="followup",
            mastery_after=None,
        )
        return FollowupResult(
            valid=True, reason=judgement.reason, round_no=round_no, answer=judgement.answer
        )

    def _all_taught_claims(self, session_id: str, entry_id: str) -> list[dict[str, Any]]:
        """深度契约输入：该条目**全部轮次**教学论断的累积（带 claim_type + round_no，D2 可重建）。

        深度契约语义是"已教过即可考"——只取最近一轮会浪费此前轮次的教学素材，
        且防重考（previous_questions）已阻止旧角度重问。按文本去重（极端情况
        审核回流后重写论断与旧轮撞文本时保留首现）。
        """
        seen: set[str] = set()
        claims: list[dict[str, Any]] = []
        for event in self._store.load_events(session_id, limit=500):
            if event.event_type != "teach_delivered":
                continue
            if event.payload.get("entry_id") != entry_id:
                continue
            round_no = event.payload.get("round_no")
            for c in event.payload.get("claims", []):
                if c["text"] in seen:
                    continue
                seen.add(c["text"])
                claim: dict[str, Any] = {"text": c["text"], "claim_type": c.get("claim_type", "core")}
                if round_no is not None:
                    claim["round_no"] = int(round_no)
                claims.append(claim)
        return claims

    def _taught_previously(self, session_id: str, entry_id: str) -> list[str]:
        """重教去重输入：该条目此前**所有**轮次的已教论断文本（时间正序）。"""
        texts: list[str] = []
        for event in self._store.load_events(session_id, limit=500):
            if event.event_type == "teach_delivered" and event.payload.get("entry_id") == entry_id:
                texts.extend(c["text"] for c in event.payload.get("claims", []))
        return texts

    # ---------- 作答与决策 ----------

    async def handle_answer(
        self, ctx: SessionContext, entry_id: str, question: Question, answer: str
    ) -> RoundResult:
        # 轮号前置计算 + 原子占用（2026-08-29）：判分含数秒级 LLM 调用，
        # 不占位会让并发双提交穿入同轮（重复判分/双写快照/双发决策事件；
        # 后到者拿到 409）——占用失败即无待答题目。
        rounds = self._store.load_rounds(ctx.session_id, entry_id)
        # 主轮过滤：追问侧车轮占用号段，不过滤会让 update_round_answer 错位到追问行
        round_no = max(
            (
                r["round_no"]
                for r in rounds
                if not str((r.get("question") or {}).get("question_id", "")).endswith("_followup")
            ),
            default=1,
        )
        if not self._store.try_claim_round(ctx.session_id, entry_id, round_no):
            raise KeyError("当前无待答题目，请先出题")
        correctness = self._store.load_mastery_history(ctx.session_id, entry_id)
        try:
            outcome = await process_answer(
                self._provider,
                question,
                answer,
                correctness,
                model=self._settings.feedback_model,
            )
        except Exception:
            # 判分失败释放占用，轮回置 pending——学生可重试，不卡死在 grading 态
            self._store.release_round_claim(ctx.session_id, entry_id, round_no)
            raise
        is_scaffold = question.question_id.endswith("_scaffold")

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
        """资源沉淀（PRD FR-11~15）：三形态 + 溯源 + 进阶标记。

        讲义输入为该条目全部轮次的论断累积（各轮互补，只取最后一轮会丢内容）。
        """
        claims, reviews, round_by_index = self._all_teach_with_verdicts(ctx.session_id, entry.id)
        rounds = self._store.load_rounds(ctx.session_id, entry.id)
        practice = extract_practice(claims, reviews, knowledge_type=entry.knowledge_type)
        challenge = build_challenge(entry.title, mastery=mastery)
        tier = difficulty_tier_for(ctx.difficulty_level, entry.difficulty)
        self._store.upsert_package(
            ctx.session_id,
            ctx.learner_id,
            entry.id,
            lecture=build_lecture(claims, reviews, round_by_index=round_by_index),
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

    def _all_teach_with_verdicts(
        self, session_id: str, entry_id: str
    ) -> tuple[list[DraftClaim], list[ReviewNote], dict[int, int]]:
        """该条目**全部** teach_delivered 事件的论断 + 裁决累积（讲义组装输入）。

        - claim_index 是事件内局部编号——跨事件合并必须全局重编号，verdicts
          同步 base 偏移（错位会让裁决落空，fail-closed 全记 unsupported 的
          旧坑，见 evals/run.py 同源处理）；
        - 按文本去重：审核回流重写后的论断与旧轮撞文本时保留首现（含裁决）；
        - round_by_index 记录每条论断的来源轮次（讲义"round"字段）。
        """
        claims: list[DraftClaim] = []
        notes: list[ReviewNote] = []
        round_by_index: dict[int, int] = {}
        seen_texts: set[str] = set()
        for event in self._store.load_events(session_id, limit=500):
            if event.event_type != "teach_delivered":
                continue
            if event.payload.get("entry_id") != entry_id:
                continue
            round_no = int(event.payload.get("round_no", 1))
            base = len(claims)
            # 局部 claim_index → 全局 claim_index（被去重丢弃的论断不进映射）
            index_map: dict[int, int] = {}
            for c in event.payload.get("claims", []):
                if c["text"] in seen_texts:
                    continue
                seen_texts.add(c["text"])
                global_index = base + len(index_map)
                index_map[int(c["claim_index"])] = global_index
                claims.append(
                    DraftClaim(
                        claim_index=global_index,
                        text=c["text"],
                        evidence_ids=c["evidence_ids"],
                        claim_type=c.get("claim_type", "core"),
                    )
                )
                round_by_index[global_index] = round_no
            for idx, verdict in event.payload.get("verdicts", {}).items():
                global_index = index_map.get(int(idx))
                if global_index is None:
                    continue
                notes.append(
                    ReviewNote(
                        claim_index=global_index,
                        verdict=verdict,  # type: ignore[arg-type]
                        reason="来自 teach_delivered 事件",
                    )
                )
        return claims, notes, round_by_index

    # ---------- 进度推导 ----------

    def progress(self, session_id: str, entry_id: str) -> TopicProgress:
        """主题进度 = 教学轮历史 + 掌握度历史的纯推导（D2）。"""
        return TopicProgress(
            rounds=self._store.load_rounds(session_id, entry_id),
            correctness=self._store.load_mastery_history(session_id, entry_id),
        )

    def rebuild_context(self, session_id: str) -> SessionContext:
        """从 DB 重建会话上下文（api 层跨请求复用；D2：无内存会话态）。"""
        session = self._store.get_session(session_id)
        if session is None:
            raise KeyError(f"会话 {session_id} 不存在")
        plan = Plan(
            topics=[
                PlanTopic(
                    entry_id=t["entry_id"],
                    title=t["title"],
                    order=t["order"],
                    target=t.get("target", True),
                )
                for t in session["plan"].get("topics", [])
            ],
            uncovered_gaps=session["plan"].get("uncovered_gaps", []),
        )
        return SessionContext(
            session_id=session_id,
            learner_id=session["learner_id"],
            profile=LearnerProfile(**session["profile"]),
            difficulty_level=session["difficulty_level"] or "beginner",
            profile_summary=session["profile_summary"] or "",
            gap_ids=session["gap_ids"],
            plan=plan,
            entries=self._entries_for(session["domain"]),
            domain=session["domain"],
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
    def question_from_record(record: dict[str, Any]) -> Question:
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


__all__ = [
    "FollowupResult",
    "RoundResult",
    "SessionContext",
    "TeachLoop",
    "TeachResult",
    "TopicProgress",
]
