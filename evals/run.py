"""批量评测（W2.3）：跑 N 组画像会话 → 三指标 JSON 报告。

用法：
  uv run python evals/run.py --limit 5          # 小批先验幻觉率（路线图 2.3 纪律）
  uv run python evals/run.py                    # 全量 50 组
  uv run python evals/run.py --profile p01      # 单组调试

断点续跑：评测结果逐组落盘 evals/results/<profile>.json，重跑跳过已存在组；
汇总报告写 evals/results/report.json。

三指标口径全部来自 evals/metrics.py（SSOT）：
- 幻觉率：从 teach_delivered 事件的 claims+verdicts 聚合（最终轮裁决）
- 画像-资源适配率：资源包 difficulty_tier 非 capped 占比
- 知识点覆盖率：目标条目 keywords 在讲义文本的字符覆盖比例

学生模拟：sim 学生（answer 用 LLM 生成自然作答，与 CLI 同策略）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

import structlog

from core.config import Settings
from core.embedding import BGEEncoder
from core.graph import build_teach_graph
from core.llm import LLMProvider
from core.mastery import compute_mastery
from core.plan import KnowledgeEntry
from core.retrieval import Retriever
from core.session import SessionStore
from core.state import DraftClaim, LearnerProfile, ReviewNote
from core.teach_loop import RoundResult, TeachLoop
from evals.metrics import hallucination_rate, keyword_coverage, tier_match_rate

logger = structlog.get_logger()

PROFILES_DIR = Path(__file__).parent / "profiles"
RESULTS_DIR = Path(__file__).parent / "results"

SIM_STUDENT_PROMPT = """你是一个刚学完下面内容的学生，正在回答老师的检验题。
用自然、口语化的中文作答（50 字内），像真实学生一样具体回应题目的场景。

【刚学的内容】
{content}

【检验题】
{prompt}

要求：作答正确（基于刚学的内容）、直接回应题目问什么、用自己的话。
只输出作答文本。"""


def load_profile_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
            id=r[0], title=r[1], content=r[2],
            prerequisites=json.loads(r[3] or "[]"),
            difficulty=r[4], keywords=json.loads(r[5] or "[]"),
            source=r[6] or "", knowledge_type=r[7] or "concept",
        )
        for r in rows
    ]


async def sim_answer(
    question, entry: KnowledgeEntry, sim_rate: float, provider, rng: random.Random
) -> str:
    """与 CLI 同策略的模拟学生：答对 = 标签 / LLM 自然作答。

    rng 为该会话独立的随机流（并发跑批下全局 random 会串组，不可复现）。
    """
    if question.question_type == "choice":
        if rng.random() < sim_rate:
            return question.expected_label
        wrong = [o[0] for o in question.options if not o.startswith(question.expected_label)]
        return rng.choice(wrong) if wrong else "Z"
    if rng.random() < sim_rate:
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
            return entry.content
    return "我还没完全学会，说不清楚。"


async def run_session(
    loop: TeachLoop, provider: LLMProvider, entries: list[KnowledgeEntry],
    profile: dict[str, Any], *, sim_rate: float, max_rounds: int,
) -> dict[str, Any]:
    learner = LearnerProfile(
        background=profile["background"],
        mastery=profile.get("mastery", {}),
        style_tags=profile.get("style_tags", []),
    )
    # 会话级独立随机流：种子编码 learner_id，重跑同画像行为可复现
    rng = random.Random(profile["learner_id"])
    ctx = await loop.start_session(profile["learner_id"], learner)
    idx = 0
    topics = ctx.plan.topics
    while idx < len(topics):
        topic = topics[idx]
        entry = ctx.entry(topic.entry_id)
        progress = loop.progress(ctx.session_id, entry.id)
        if max_rounds and progress.next_round_no > max_rounds:
            idx += 1
            continue
        if progress.needs_teaching:
            await loop.teach_round(ctx, entry.id)
        question = await loop.next_question(ctx, entry.id)
        answer = await sim_answer(question, entry, sim_rate, provider, rng)
        result: RoundResult = await loop.handle_answer(ctx, entry.id, question, answer)
        if result.decision == "advance":
            idx += 1
        elif result.decision == "regress":
            prereq = entry.prerequisites[0] if entry.prerequisites else None
            if prereq and any(t.entry_id == prereq for t in topics[:idx]):
                idx = next(i for i, t in enumerate(topics) if t.entry_id == prereq)
            else:
                idx += 1
    await loop.end_session(ctx)

    # ── 指标聚合（口径：evals/metrics.py SSOT）─────────────────────
    store = loop._store  # noqa: SLF001
    events = store.load_events(ctx.session_id, limit=2000)
    drafts: list[DraftClaim] = []
    reviews: list[ReviewNote] = []
    for ev in events:
        if ev.event_type != "teach_delivered":
            continue
        # claim_index 是事件内局部编号——聚合时 claims 与 verdicts
        # 必须同步偏移（错位会让裁决落空，fail-closed 全记 unsupported）
        base = len(drafts)
        for c in ev.payload.get("claims", []):
            drafts.append(
                DraftClaim(
                    claim_index=base + int(c["claim_index"]), text=c["text"],
                    evidence_ids=c.get("evidence_ids", ["?"]),
                    claim_type=c.get("claim_type", "core"),
                )
            )
        for i, verdict in ev.payload.get("verdicts", {}).items():
            reviews.append(
                ReviewNote(claim_index=base + int(i), verdict=verdict, reason="评测聚合")
            )
    packages = store.load_packages(ctx.session_id)
    entry_map = {e.id: e for e in entries}
    keywords = {
        e.id: e.keywords for e in entries
        if e.id in {p["entry_id"] for p in packages}
    }
    tier_rate, tier_m, tier_t = tier_match_rate(packages)
    cov_rate, cov_hit, cov_total = keyword_coverage(packages, keywords)
    masteries = {
        entry_map[t.entry_id].title: compute_mastery(
            store.load_mastery_history(ctx.session_id, t.entry_id)
        )
        for t in topics
        if t.entry_id in entry_map
    }
    return {
        "learner_id": profile["learner_id"],
        "name": profile.get("name", ""),
        "expect_level": profile.get("expect_level", ""),
        "diagnosed_level": ctx.difficulty_level,
        "session_id": ctx.session_id,
        "hallucination_rate": round(hallucination_rate(drafts, reviews), 4),
        "claims_total": len(drafts),
        "tier_match": {"rate": round(tier_rate, 4), "matched": tier_m, "total": tier_t},
        "keyword_coverage": {"rate": round(cov_rate, 4), "hit": cov_hit, "total": cov_total},
        "topics_advanced": len(packages),
        "mastery_mean": round(sum(masteries.values()) / len(masteries), 4) if masteries else 0.0,
        "masteries": {k: round(v, 3) for k, v in masteries.items()},
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="批量评测：跑画像会话产出三指标")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 组（0=全部）")
    parser.add_argument("--profile", default=None, help="单组调试（learner_id，如 p01）")
    parser.add_argument("--sim", type=float, default=0.8, help="模拟学生答对率")
    parser.add_argument("--max-rounds", type=int, default=4, help="每主题轮数上限")
    parser.add_argument("--concurrency", type=int, default=5, help="并发会话数（防 API 429）")
    parser.add_argument("--force", action="store_true", help="忽略断点缓存重跑")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    profile_files = sorted(PROFILES_DIR.glob("p*.json"))
    if args.profile:
        profile_files = [PROFILES_DIR / f"{args.profile}.json"]
        if not profile_files[0].exists():
            raise SystemExit(f"画像不存在: {profile_files[0]}")
    if args.limit:
        profile_files = profile_files[: args.limit]

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

    results: list[dict[str, Any]] = []
    pending: list[tuple[Path, dict[str, Any]]] = []
    for pf in profile_files:
        profile = load_profile_json(pf)
        out_path = RESULTS_DIR / f"{profile['learner_id']}.json"
        if out_path.exists() and not args.force:
            logger.info("eval_skip_cached", learner=profile["learner_id"])
            results.append(json.loads(out_path.read_text(encoding="utf-8")))
            continue
        pending.append((out_path, profile))

    # 并发跑批：瓶颈在 LLM 网络 IO（单组 40-60 次调用 ~3 分钟），
    # SQLite 同步写在单线程 loop 内天然串行化，Semaphore 限流防 API 429。
    sem = asyncio.Semaphore(args.concurrency)

    async def _run_one(out_path: Path, profile: dict[str, Any]) -> dict[str, Any] | None:
        async with sem:
            t0 = time.time()
            try:
                r = await run_session(
                    loop, provider, entries, profile,
                    sim_rate=args.sim, max_rounds=args.max_rounds,
                )
            except Exception as exc:
                logger.error("eval_session_failed", learner=profile["learner_id"], error=str(exc)[:200])
                return None
            out_path.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            logger.info(
                "eval_done",
                learner=profile["learner_id"],
                halluc=r["hallucination_rate"],
                tier=r["tier_match"]["rate"],
                cov=r["keyword_coverage"]["rate"],
                secs=round(time.time() - t0, 1),
            )
            return r

    done = await asyncio.gather(*[_run_one(o, p) for o, p in pending])
    results.extend(r for r in done if r is not None)

    if not results:
        raise SystemExit("无有效评测结果")
    level_total = sum(1 for r in results if r["expect_level"])
    report = {
        "n_profiles": len(results),
        "sim_rate": args.sim,
        "hallucination_rate": round(
            sum(r["claims_total"] * r["hallucination_rate"] for r in results)
            / max(sum(r["claims_total"] for r in results), 1),
            4,
        ),
        "tier_match_rate": round(
            sum(r["tier_match"]["matched"] for r in results)
            / max(sum(r["tier_match"]["total"] for r in results), 1),
            4,
        ),
        "keyword_coverage": round(
            sum(r["keyword_coverage"]["hit"] for r in results)
            / max(sum(r["keyword_coverage"]["total"] for r in results), 1),
            4,
        ),
        "mastery_mean": round(sum(r["mastery_mean"] for r in results) / len(results), 4),
        "level_accuracy": (
            round(
                sum(
                    1
                    for r in results
                    if r["expect_level"] and r["expect_level"] == r["diagnosed_level"]
                )
                / level_total,
                4,
            )
            if level_total
            else "N/A（无对照画像）"
        ),
        "targets": {"hallucination_rate": "<0.05", "tier_match_rate": ">=0.85", "keyword_coverage": ">=0.90"},
    }
    (RESULTS_DIR / "report.json").write_text(
        json.dumps({"summary": report, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    from core.logging import configure_logging

    configure_logging()
    asyncio.run(main())
