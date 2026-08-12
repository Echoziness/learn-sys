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
import time
from collections import defaultdict

import structlog
from dotenv import load_dotenv
from scripts.cli_input import Choice, ask_choice, ask_text

from core.agents.diagnose import diagnose_node
from core.agents.question import build_scaffold_distractors, question_node
from core.answer_pipeline import process_answer
from core.assess import (
    Question,
    build_question,
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

# 回答题 (题干, 判分要点) 缓存：同一主题反复教学用同一道题（可复现、省调用）
_question_cache: dict[str, tuple[str, tuple[str, ...]]] = {}


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
    """包裹一次 LLM 调用：开始前给阶段提示（防误以为卡死），结束后报耗时。"""
    start = time.monotonic()
    print(f"\n[{label}] 正在调用 LLM（约 10-60s，请稍候）…")
    result = await coro
    print(f"[{label}] 完成（{time.monotonic() - start:.1f}s）")
    return result


async def build_scaffold_question(
    topic: KnowledgeEntry,
    failed: dict,
    entries: list[KnowledgeEntry],
    *,
    provider: LLMProvider,
    settings: Settings,
) -> Question:
    """脚手架选择题：回答题失败后的中间台阶。

    正确项 = 条目 keywords（服务端构造，判分只认标签）；干扰项由 LLM 生成
    （首项为学生上一轮作答中的典型错误理解镜像，帮助学生对比发现自己的问题）；
    LLM 失败回退确定性干扰项（其他条目关键词，fail-closed）。
    """
    correct_text = "、".join(topic.keywords[:6]) or topic.title
    dists: list[str] = []
    try:
        dists = await with_llm_progress(
            "脚手架",
            build_scaffold_distractors(
                provider,
                failed["prompt"],
                failed["answer"],
                correct_text,
                model=settings.question_model,
            ),
        )
    except Exception:
        logger.warning("scaffold_llm_failed_fallback_distractors", entry_id=topic.id)
    if not dists:
        for other in entries:
            text = "、".join(other.keywords[:6])
            if text and text != correct_text and text not in dists:
                dists.append(text)
                if len(dists) >= 3:
                    break
    labels = "ABCD"
    options = [f"A. {correct_text}", *[f"{labels[i]}. {t}" for i, t in enumerate(dists, 1)]]
    return Question(
        question_id=f"q_{topic.id}_scaffold",
        entry_id=topic.id,
        prompt=f"关于上一题（{failed['prompt']}），以下哪个选项是正确的做法？",
        question_type="choice",
        expected_keywords=(),
        options=tuple(options),
        expected_label="A",
    )


async def teach_topic(
    graph,
    topic: KnowledgeEntry,
    entries: list[KnowledgeEntry],
    difficulty_level: str,
    sim_rate: float | None,
    *,
    provider: LLMProvider,
    settings: Settings,
    max_rounds: int = 0,
) -> tuple[list[bool], dict]:
    """教一个主题：教学（图）→ 出题 → 作答 → 判分 → 决策，直到 advance/regress。

    返回 (correctness 历史, 最终决策 state 片段)。correctness 供上层跨主题累积。
    """
    correctness: list[bool] = []
    round_no = 0
    final: dict = {}
    retry_context = ""
    reached_answer = False  # 题型单向推进：进入回答深度后不再降回选择题
    scaffold_pending = False  # 回答题失败 → 下一轮先出脚手架选择题
    failed_question: dict = {}  # 失败的回答题（题目+作答），供脚手架镜像干扰项
    while True:
        round_no += 1
        if max_rounds and round_no > max_rounds:
            print(f"\n[决策] 达到验证轮数上限（{max_rounds}），跳过剩余教学。")
            break
        print_section(f"教学单元（第 {round_no} 轮）：{topic.title}")
        state: AgentState = {
            "learner_id": "session",
            "gaps": [topic.title],
            "anchor_entry": topic,
            "difficulty_level": difficulty_level,
            "review_round": 0,
        }
        if retry_context:
            state["retry_context"] = retry_context
        final = await with_llm_progress("教学", graph.ainvoke(state))

        draft = final.get("draft", [])
        for claim in draft:
            tag = " [错因扩展]" if claim.claim_type == "extension" else ""
            print(f"\n{claim.text}{tag}")
            print(f"  └─ 来源: {', '.join(claim.evidence_ids)}")
        reviews = final.get("review_history", [])
        bad = [r for r in reviews if r.verdict != "supported"]
        if bad:
            print(f"\n[审核] {len(bad)} 条论断未获支持，本轮教学质量存疑")
        else:
            print("\n[审核] 全部论断通过")

        if scaffold_pending and failed_question:
            question = await build_scaffold_question(
                topic, failed_question, entries, provider=provider, settings=settings
            )
            scaffold_pending = False  # 已出题，结果决定是否再置位
            print("\n[脚手架] 上一题回答不理想——先做一个选择题确认关键点，再回来回答。")
        else:
            question = build_question(
                topic,
                distractors=entries,
                mastery=compute_mastery(correctness),
                floor_type="answer" if reached_answer else None,
            )
            if question.question_type == "answer":
                reached_answer = True
            if question.question_type == "answer" and topic.id not in _question_cache:
                try:
                    q = await with_llm_progress(
                        "出题",
                        question_node(
                            {
                                "entry": {
                                    "id": topic.id,
                                    "title": topic.title,
                                    "content": topic.content,
                                    "keywords": topic.keywords,
                                },
                                "taught_claims": [c.text for c in draft],
                            },
                            provider=provider,
                            model=settings.question_model,
                        ),
                    )
                    if q["question"]:
                        _question_cache[topic.id] = (q["question"], tuple(q["expected_keywords"]))
                except Exception:
                    logger.warning("question_llm_failed_fallback_template", entry_id=topic.id)
            cached = _question_cache.get(topic.id)
            if question.question_type == "answer" and cached is not None:
                prompt, expected = cached
                question = Question(
                    question_id=question.question_id,
                    entry_id=question.entry_id,
                    prompt=prompt,
                    question_type=question.question_type,
                    # LLM 校验通过的要点优先；为空（校验全失败）回退条目 keywords。
                    expected_keywords=expected or question.expected_keywords,
                )
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
                answer = topic.content
            else:
                answer = "我还没完全学会，说不清楚。"
        else:
            if question.question_type == "choice":
                choices = [
                    Choice(label=opt, value=opt[0]) for opt in question.options
                ]
                answer = ask_choice(f"[你的作答] {question.prompt}", choices)
            else:
                answer = ask_text("[你的作答] ")
            if not answer:
                print("\n[退出] 未作答，结束会话。")
                break

        outcome = await with_llm_progress(
            "判分",
            process_answer(
                provider,
                question,
                answer,
                correctness,
                model=settings.feedback_model,
            ),
        )
        # 脚手架轮不计入掌握度历史：答对=识别通过（教学台阶，非测评），
        # 不打断"连续 2 次回答题失败 → 降维"的计数；答错则计一次错（识别都没过）。
        is_scaffold = question.question_id.endswith("_scaffold")
        if not is_scaffold or not outcome.is_correct:
            correctness.append(outcome.is_correct)
        print(f"[反馈] {'✓ 正确' if outcome.is_correct else '✗ 不完整'} "
              f"（覆盖率 {outcome.grade.keyword_coverage:.0%}）")
        print(outcome.evaluation)

        decision, mastery = outcome.decision, outcome.mastery
        logger.info(
            "topic_round",
            entry_id=topic.id,
            round=round_no,
            decision=decision,
            mastery=round(mastery, 3),
            attempts=len(correctness),
            llm_reviewed=outcome.llm_reviewed,
        )
        if decision == "advance":
            tag = "（轮次上限放行，未达门槛）" if mastery < 0.7 else ""
            print(f"\n[决策] 本主题已达标，进入下一主题。{tag}")
            break
        if decision == "regress":
            print("\n[决策] 连续答错，判定地基未打牢——回前置主题重新教。")
            break
        print("\n[决策] 继续本主题：针对你的作答重新讲一遍。")
        # 脚手架状态机：回答题失败 → 下轮出脚手架选择题；脚手架答对 → 回回答题
        if question.question_type == "answer" and not outcome.is_correct:
            scaffold_pending = True
            failed_question = {"prompt": question.prompt, "answer": answer}
        elif question.question_id.endswith("_scaffold"):
            scaffold_pending = not outcome.is_correct  # 答对=注意到问题，回回答题
        # 错因回流：下一轮教学直接回应本轮作答的偏差
        if question.question_type == "choice" and outcome.is_correct:
            ctx_hint = (
                "上一轮为选择题且回答正确（识别已通过）——本次重教不得复读基础定义，"
                "请向应用深度推进：讲概念的实际应用场景、常见误解与易错点。"
            )
        else:
            ctx_hint = ""
        retry_context = (
            f"{ctx_hint}\n题目：{question.prompt}\n"
            f"学生作答：{answer}\n"
            f"评估：{outcome.evaluation}"
        ).strip("\n")
        # 教学加深后题目必须重新生成（旧题基于旧教学内容，深度契约失效）
        _question_cache.pop(topic.id, None)

    return correctness, final


async def main() -> None:
    parser = argparse.ArgumentParser(description="learn-sys 会话 CLI")
    parser.add_argument("learner_id", nargs="?", default="test1")
    parser.add_argument("--sim", type=float, default=None,
                        help="模拟学生模式：按 RATE 概率答对（0-1）")
    parser.add_argument("--max-rounds", type=int, default=0,
                        help="每个主题最多教学轮数（默认 0=不限制；验证用 1-2 即可）")
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
    diag = await with_llm_progress(
        "诊断",
        diagnose_node(
            {"learner_profile": profile, "test_results": []},
            provider=provider,
            model=settings.diagnose_model,
            entry_catalog=catalog,
        ),
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
            teach_graph,
            entry,
            entries,
            diag["difficulty_level"],
            args.sim,
            provider=provider,
            settings=settings,
            max_rounds=args.max_rounds,
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
