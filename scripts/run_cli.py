#!/usr/bin/env python3
"""会话 CLI 入口（组合根 + 表现层）：装配依赖 → 驱动 teach_loop → 终端呈现。

编排逻辑全部在 core/teach_loop（CLI 与 Web 共用）；本文件只做三件事：
装配（provider/retriever/graph/store/entries）、驱动（主题遍历循环）、
呈现（打印与输入）。无业务判断。

用法：
  uv run python scripts/run_cli.py [learner_id] [--sim RATE] [--max-rounds N] [--out PATH]
  --sim RATE  模拟学生模式：按 RATE 概率答对（0-1），不传则手动输入作答
"""

import argparse
import asyncio
import json
import random
import sqlite3
import time

import structlog
from dotenv import load_dotenv
from scripts.cli_input import Choice, ask_choice, ask_text

from core.assess import Question
from core.config import Settings
from core.embedding import BGEEncoder
from core.graph import build_teach_graph
from core.llm import LLMProvider
from core.logging import configure_logging
from core.mastery import compute_mastery, decide_next_step
from core.plan import KnowledgeEntry
from core.retrieval import Retriever
from core.session import SessionStore
from core.state import LearnerProfile
from core.teach_loop import RoundResult, SessionContext, TeachLoop, TeachResult

logger = structlog.get_logger()


def load_profile(db_path: str, learner_id: str) -> LearnerProfile:
    db = sqlite3.connect(db_path)
    try:
        row = db.execute(
            "SELECT background, mastery, style_tags FROM learner_profiles WHERE learner_id=?",
            (learner_id,),
        ).fetchone()
    finally:
        db.close()
    if row is None:
        raise SystemExit(f"学习者 {learner_id} 不存在，请先运行 scripts/init_db.py")
    return LearnerProfile(
        background=json.loads(row[0]),
        mastery=json.loads(row[1]),
        style_tags=json.loads(row[2]),
    )


def load_entries(db_path: str, domain: str = "bigdata-analysis") -> list[KnowledgeEntry]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT id, title, content, prerequisites, difficulty, keywords, source, knowledge_type "
            "FROM knowledge_entries WHERE domain=?",
            (domain,),
        ).fetchall()
    finally:
        db.close()
    return [
        KnowledgeEntry(
            id=r[0],
            title=r[1],
            content=r[2],
            prerequisites=json.loads(r[3] or "[]"),
            difficulty=r[4],
            keywords=json.loads(r[5] or "[]"),
            source=r[6] or "",
            knowledge_type=r[7] or "concept",
        )
        for r in rows
    ]


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


async def with_llm_progress(label: str, coro):
    """包裹一次 LLM 环节：开始前给阶段提示（防误以为卡死），结束后报耗时。"""
    start = time.monotonic()
    print(f"\n[{label}] 正在调用 LLM（约 3-30s，请稍候）…")
    result = await coro
    print(f"[{label}] 完成（{time.monotonic() - start:.1f}s）")
    return result


def print_teach(result: TeachResult) -> None:
    for claim in result.claims:
        tag = " [错因扩展]" if claim.claim_type == "extension" else ""
        tag = " [实操指南]" if claim.claim_type == "procedure_guide" else tag
        print(f"\n{claim.text}{tag}")
        print(f"  └─ 来源: {', '.join(claim.evidence_ids)}")
    bad = [r for r in result.reviews if r.verdict != "supported"]
    if bad:
        print(f"\n[审核] {len(bad)} 条论断未获支持，本轮教学质量存疑")
    else:
        print("\n[审核] 全部论断通过")


def print_question(question: Question) -> None:
    print(f"\n[检验] {question.prompt}")
    for opt in question.options:
        print(f"  {opt}")


def print_feedback(result: RoundResult) -> None:
    o = result.outcome
    print(f"[反馈] {'✓ 正确' if o.is_correct else '✗ 不完整'} "
          f"（覆盖率 {o.grade.keyword_coverage:.0%}）")
    if o.missed_requirements:
        print(f"[题意核对] 遗漏要求: {'；'.join(o.missed_requirements)}")
    print(o.evaluation)
    if result.decision == "advance":
        tag = "（轮次上限放行，未达门槛）" if result.mastery < 0.7 else ""
        print(f"\n[决策] 本主题已达标，进入下一主题。{tag}")
    elif result.decision == "regress":
        print("\n[决策] 连续答错，判定地基未打牢——回前置主题重新教。")
    elif result.is_scaffold and result.outcome.is_correct:
        print("\n[决策] 关键点已确认，回到回答题检验。")
    elif result.question.question_type == "answer" and result.outcome.is_correct:
        print(f"\n[决策] 已答对，掌握度 {result.mastery:.3f}（门槛 0.700）——下一题直接确认巩固。")
    elif result.question.question_type == "choice" and result.outcome.is_correct:
        print("\n[决策] 识别已通过——本轮教学将向应用深度推进。")
    else:
        print("\n[决策] 继续本主题：针对你的错因重新讲解。")


SIM_STUDENT_PROMPT = """你是一个刚学完下面内容的学生，正在回答老师的检验题。
用自然、口语化的中文作答（50 字内），像真实学生一样具体回应题目的场景。

【刚学的内容】
{content}

【检验题】
{prompt}

要求：作答正确（基于刚学的内容）、直接回应题目问什么、用自己的话。
只输出作答文本。"""


async def sim_answer(
    question: Question, entry: KnowledgeEntry, sim_rate: float, provider
) -> str:
    """模拟学生：按概率答对。

    choice 答对 = 正确标签；answer 答对 = LLM 生成自然学生作答
    （关键词堆/空洞串句会被复核判"只列概念无理解"——那不是理解，
    生成真实作答才能让 sim 通过率反映判分真实性）。
    """
    if question.question_type == "choice":
        if random.random() < sim_rate:
            return question.expected_label
        wrong = [o[0] for o in question.options if not o.startswith(question.expected_label)]
        return random.choice(wrong) if wrong else "Z"
    if random.random() < sim_rate:
        try:
            return await provider.chat(
                [
                    {
                        "role": "user",
                        "content": SIM_STUDENT_PROMPT.format(
                            content=entry.content, prompt=question.prompt
                        ),
                    }
                ],
                temperature=0.2,
            )
        except Exception:
            return entry.content  # LLM 失败回退：复述条目原文（要点齐全）
    return "我还没完全学会，说不清楚。"


async def run(args) -> None:
    settings = Settings.from_env()
    base_url, api_key, model = settings.llm_fields()
    provider = LLMProvider(
        base_url=base_url, api_key=api_key, model=model,
        extra_body=settings.llm_extra_body,
    )
    retriever = Retriever(
        db_path=settings.database_path,
        encoder=BGEEncoder(cache_folder=settings.bge_model_path, local_files_only=True),
        rrf_k=settings.rrf_k,
        coverage_min_score=settings.coverage_min_score,
    )
    graph = build_teach_graph(settings, provider, retriever)
    store = SessionStore(settings.database_path)
    entries = load_entries(settings.database_path)
    loop = TeachLoop(graph=graph, provider=provider, store=store, settings=settings, entries=entries)

    profile = load_profile(settings.database_path, args.learner_id)
    print_section(f"学情画像输入（{args.learner_id}）")
    print(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2))

    print_section("诊断")
    ctx: SessionContext = await with_llm_progress("诊断", loop.start_session(args.learner_id, profile))
    print(f"画像摘要: {ctx.profile_summary}")
    gap_titles = [e.title for e in entries if e.id in ctx.gap_ids]
    print(f"知识盲区: {gap_titles or ctx.gap_ids}")
    print(f"难度水平: {ctx.difficulty_level}")

    print_section("课程切片")
    for t in ctx.plan.topics:
        mark = "◎目标" if t.target else "○前置链补入"
        print(f"  {t.order}. [{mark}] {t.title} ({t.entry_id})")
    if ctx.plan.uncovered_gaps:
        print(f"知识库未覆盖（不教，如实告知）: {ctx.plan.uncovered_gaps}")

    print_section("导学会话")
    idx = 0
    while idx < len(ctx.plan.topics):
        topic = ctx.plan.topics[idx]
        entry = ctx.entry(topic.entry_id)
        progress = loop.progress(ctx.session_id, entry.id)
        if args.max_rounds and progress.next_round_no > args.max_rounds:
            print(f"\n[决策] 达到验证轮数上限（{args.max_rounds}），跳过剩余教学。")
            idx += 1
            continue

        print_section(f"教学单元（第 {progress.next_round_no} 轮）：{entry.title}")
        if progress.needs_teaching:
            teach = await with_llm_progress("教学", loop.teach_round(ctx, entry.id))
            print_teach(teach)
        else:
            # 巩固模式：answer 已答对（未达门槛只是证据不足）——直接出题确认，
            # 不再讲课（教学无锚点时 LLM 只能复读；mastery 证据由作答累积）
            print("[巩固] 上一题已答对——跳过教学，直接出题确认掌握。")

        question = await with_llm_progress("出题", loop.next_question(ctx, entry.id))
        if question.question_id.endswith("_scaffold"):
            print("\n[脚手架] 上一题回答不理想——先做一个选择题确认关键点，再回来回答。")
        print_question(question)

        if args.sim is not None:
            answer = await sim_answer(question, entry, args.sim, provider)
        else:
            if question.question_type == "choice":
                choices = [Choice(label=opt, value=opt[0]) for opt in question.options]
                answer = ask_choice(f"[你的作答] {question.prompt}", choices)
            else:
                answer = ask_text("[你的作答] ")
            if not answer:
                print("\n[退出] 未作答，结束会话。")
                break

        result = await with_llm_progress("判分", loop.handle_answer(ctx, entry.id, question, answer))
        print_feedback(result)
        logger.info(
            "topic_round",
            entry_id=entry.id,
            round=result.round_no,
            decision=result.decision,
            mastery=round(result.mastery, 3),
            llm_reviewed=result.outcome.llm_reviewed,
        )

        if result.decision == "advance":
            idx += 1
            continue
        if result.decision == "regress":
            history = store.load_mastery_history(ctx.session_id, entry.id)
            d, _ = decide_next_step(history)
            if d == "regress":
                prereq_id = entry.prerequisites[0] if entry.prerequisites else None
                if prereq_id and any(t.entry_id == prereq_id for t in ctx.plan.topics[:idx]):
                    print_section("降维")
                    print(f"回到前置主题重新教: {prereq_id}")
                    idx = next(i for i, t in enumerate(ctx.plan.topics) if t.entry_id == prereq_id)
                    continue
                print("[提示] 无前置主题可退，标记未达标并继续。")
            idx += 1

    print_section("掌握度汇总")
    for t in ctx.plan.topics:
        history = store.load_mastery_history(ctx.session_id, t.entry_id)
        mastery = compute_mastery(history)
        print(f"  {t.title}: mastery={mastery:.2f}（{len(history)} 次作答）")

    packages = store.load_packages(ctx.session_id)
    print(f"\n资源包: {len(packages)} 个主题（讲义 {sum(len(p['lecture']) for p in packages)} 条论断，"
          f"题目 {sum(len(p['questions']) for p in packages)} 道，"
          f"实操指南 {sum(1 for p in packages if p['practice'])} 份，"
          f"进阶挑战 {sum(1 for p in packages if p['challenge'])} 个）")
    print(f"事件流: {len(store.load_events(ctx.session_id))} 条（session_id={ctx.session_id}）")

    await loop.end_session(ctx)
    logger.info("session_done", learner_id=args.learner_id, session_id=ctx.session_id,
                topics=len(ctx.plan.topics))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {"session_id": ctx.session_id, "packages": packages},
                f, ensure_ascii=False, indent=2,
            )
        logger.info("output_written", path=args.out)


async def main() -> None:
    parser = argparse.ArgumentParser(description="learn-sys 会话 CLI")
    parser.add_argument("learner_id", nargs="?", default="test1")
    parser.add_argument("--sim", type=float, default=None,
                        help="模拟学生模式：按 RATE 概率答对（0-1）")
    parser.add_argument("--max-rounds", type=int, default=0,
                        help="每个主题最多教学轮数（默认 0=不限制；验证用 1-2 即可）")
    parser.add_argument("--out", default=None, help="将会话产出（资源包）写出为 JSON 文件")
    args = parser.parse_args()

    load_dotenv()
    configure_logging()
    await run(args)


if __name__ == "__main__":
    asyncio.run(main())
