#!/usr/bin/env python3
"""会话 CLI 入口（组合根）：装配依赖 → 诊断 → 切片 → 逐主题教学 → 问答循环。

全链路（2026-07-22 会话化）：
  diagnose（LLM，一次）→ plan（确定性切片）→ 逐主题：
    teach_graph（检索→生成→审核）→ assess（确定性出题）→ 学生作答
    → feedback（判分+掌握度）→ 进/停/退（advance / retry / regress 降维）

用法：
  uv run python scripts/run_cli.py [learner_id] [--sim RATE] [--out PATH]
  --sim RATE  模拟学生模式：按 RATE 概率答对（0-1），不传则手动输入作答
"""

import argparse
import asyncio
import json
import random
import sqlite3
from collections import defaultdict

import structlog
from dotenv import load_dotenv

from core.agents.diagnose import diagnose_node
from core.assess import (
    GradeResult,
    build_feedback_message,
    build_question,
    grade_answer,
)
from core.config import Settings
from core.embedding import BGEEncoder
from core.graph import build_teach_graph
from core.llm import LLMProvider
from core.logging import configure_logging
from core.mastery import compute_mastery, decide_next_step
from core.plan import KnowledgeEntry, build_plan
from core.retrieval import Retriever
from core.state import AgentState, LearnerProfile
from evals.metrics import hallucination_rate

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


async def teach_topic(
    graph,
    topic: KnowledgeEntry,
    entries: list[KnowledgeEntry],
    difficulty_level: str,
    sim_rate: float | None,
) -> tuple[list[bool], dict]:
    """教一个主题：教学（图）→ 出题 → 作答 → 判分 → 决策，直到 advance/regress。

    返回 (correctness 历史, 最终决策 state 片段)。correctness 供上层跨主题累积。
    """
    correctness: list[bool] = []
    round_no = 0
    final: dict = {}
    while True:
        round_no += 1
        print_section(f"教学单元（第 {round_no} 轮）：{topic.title}")
        state: AgentState = {
            "learner_id": "session",
            "gaps": [topic.title],
            "difficulty_level": difficulty_level,
            "review_round": 0,
        }
        final = await graph.ainvoke(state)

        draft = final.get("draft", [])
        for claim in draft:
            print(f"\n{claim.text}")
            print(f"  └─ 来源: {', '.join(claim.evidence_ids)}")
        reviews = final.get("review_history", [])
        bad = [r for r in reviews if r.verdict != "supported"]
        if bad:
            print(f"\n[审核] {len(bad)} 条论断未获支持，本轮教学质量存疑")
        else:
            print("\n[审核] 全部论断通过")

        question = build_question(topic, distractors=entries, mastery=compute_mastery(correctness))
        print(f"\n[检验] {question.prompt}")
        for opt in question.options:
            print(f"  {opt}")
        if sim_rate is not None:
            if question.question_type == "choice":
                if random.random() < sim_rate:
                    answer = question.expected_label
                else:
                    wrong = [o[0] for o in question.options if not o.startswith(question.expected_label)]
                    answer = random.choice(wrong) if wrong else "Z"
            elif random.random() < sim_rate:
                answer = "、".join(question.expected_keywords) + "。"
            else:
                answer = "我还没完全学会，说不清楚。"
        else:
            try:
                if question.question_type == "choice":
                    labels = "/".join(o[0] for o in question.options)
                    answer = input(f"[你的作答]（输入选项字母 {labels}，如 A）：").strip()
                else:
                    answer = input("[你的作答]：").strip()
            except EOFError:
                answer = ""
            if not answer:
                print("\n[退出] 未作答，结束会话。")
                break
        grade: GradeResult = grade_answer(question, answer)
        correctness.append(grade.is_correct)
        print(f"[反馈] {'✓ 正确' if grade.is_correct else '✗ 不完整'} "
              f"（覆盖率 {grade.keyword_coverage:.0%}）")
        print(build_feedback_message(grade, question))

        decision, mastery = decide_next_step(correctness)
        logger.info(
            "topic_round",
            entry_id=topic.id,
            round=round_no,
            decision=decision,
            mastery=round(mastery, 3),
            attempts=len(correctness),
        )
        if decision == "advance":
            tag = "（轮次上限放行，未达门槛）" if mastery < 0.7 else ""
            print(f"\n[决策] 本主题已达标，进入下一主题。{tag}")
            break
        if decision == "regress":
            print("\n[决策] 连续答错，判定地基未打牢——回前置主题重新教。")
            break
        print("\n[决策] 继续本主题：换个方式再讲一遍。")

    return correctness, final


async def main() -> None:
    parser = argparse.ArgumentParser(description="learn-sys 会话 CLI")
    parser.add_argument("learner_id", nargs="?", default="test1")
    parser.add_argument("--sim", type=float, default=None,
                        help="模拟学生模式：按 RATE 概率答对（0-1）")
    parser.add_argument("--out", default=None, help="将最终 state 写出为 JSON 文件")
    args = parser.parse_args()

    load_dotenv()
    configure_logging()
    settings = Settings.from_env()

    base_url, api_key, model = settings.llm_fields()
    provider = LLMProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        extra_body=settings.llm_extra_body,
    )
    retriever = Retriever(
        db_path=settings.database_path,
        encoder=BGEEncoder(cache_folder=settings.bge_model_path, local_files_only=True),
        rrf_k=settings.rrf_k,
        coverage_min_score=settings.coverage_min_score,
    )
    teach_graph = build_teach_graph(settings, provider, retriever)

    profile = load_profile(settings.database_path, args.learner_id)
    entries = load_entries(settings.database_path)
    print_section(f"学情画像输入（{args.learner_id}）")
    print(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2))

    print_section("诊断")
    catalog = [{"id": e.id, "title": e.title} for e in entries]
    diag = await diagnose_node(
        {"learner_profile": profile, "test_results": []},
        provider=provider,
        model=settings.diagnose_model,
        entry_catalog=catalog,
    )
    print(f"画像摘要: {diag['profile_summary']}")
    gap_titles = [c["title"] for c in catalog if c["id"] in diag["gap_ids"]]
    print(f"知识盲区: {gap_titles or diag['gaps']}")
    print(f"难度水平: {diag['difficulty_level']}")

    print_section("课程切片")
    plan = build_plan(entries, diag["gap_ids"] or diag["gaps"], max_difficulty=5)
    for t in plan.topics:
        mark = "◎目标" if t.target else "○前置链补入"
        print(f"  {t.order}. [{mark}] {t.title} ({t.entry_id})")
    if plan.uncovered_gaps:
        print(f"知识库未覆盖（不教，如实告知）: {plan.uncovered_gaps}")

    mastery_history: dict[str, list[bool]] = defaultdict(list)
    idx = 0
    final_state: dict = {}
    while idx < len(plan.topics):
        topic = plan.topics[idx]
        entry = next(e for e in entries if e.id == topic.entry_id)

        correctness, final_state = await teach_topic(
            teach_graph, entry, entries, diag["difficulty_level"], args.sim
        )
        mastery_history[topic.entry_id].extend(correctness)

        decision, _ = decide_next_step(mastery_history[topic.entry_id])
        if decision == "regress":
            prereq_id = entry.prerequisites[0] if entry.prerequisites else None
            if prereq_id and any(t.entry_id == prereq_id for t in plan.topics[:idx]):
                print_section("降维")
                print(f"回到前置主题重新教: {prereq_id}")
                idx = next(i for i, t in enumerate(plan.topics) if t.entry_id == prereq_id)
                continue
            print("[提示] 无前置主题可退，标记未达标并继续。")
        idx += 1

    print_section("掌握度汇总")
    for t in plan.topics:
        history = mastery_history.get(t.entry_id, [])
        mastery = compute_mastery(history)
        print(f"  {t.title}: mastery={mastery:.2f}（{len(history)} 次作答）")

    rate = hallucination_rate(
        final_state.get("draft", []), final_state.get("review_history", [])
    )
    print(f"\n幻觉率（最后一课）: {rate:.1%}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(final_state, f, ensure_ascii=False, indent=2, default=lambda o: o.model_dump())
        logger.info("state_written", path=args.out)

    logger.info("session_done", learner_id=args.learner_id, topics=len(plan.topics))


if __name__ == "__main__":
    asyncio.run(main())
