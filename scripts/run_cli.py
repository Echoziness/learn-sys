#!/usr/bin/env python3
"""Phase 1 CLI 入口（组合根）：装配依赖 → 从 DB 读画像 → 跑图 → 输出结果与指标。

用法：uv run python scripts/run_cli.py [learner_id] [--out PATH]
"""

import argparse
import asyncio
import json
import sqlite3

import structlog
from dotenv import load_dotenv

from core.config import Settings
from core.embedding import BGEEncoder
from core.graph import build_graph
from core.llm import LLMProvider
from core.logging import configure_logging
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


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="learn-sys Phase 1 CLI")
    parser.add_argument("learner_id", nargs="?", default="test1")
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
    graph = build_graph(settings, provider, retriever)

    profile = load_profile(settings.database_path, args.learner_id)
    print_section(f"学情画像输入（{args.learner_id}）")
    print(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2))

    initial_state: AgentState = {
        "learner_id": args.learner_id,
        "learner_profile": profile,
        "test_results": [],
        "review_round": 0,
    }
    final_state = await graph.ainvoke(initial_state)

    print_section("诊断结果")
    print(f"画像摘要: {final_state.get('profile_summary', 'N/A')}")
    print(f"知识盲区: {final_state.get('gaps', [])}")
    uncovered = final_state.get("uncovered_gaps", [])
    if uncovered:
        print(f"知识库未覆盖: {uncovered}")

    retrieved = final_state.get("retrieved_entries", [])
    print_section(f"检索条目（{len(retrieved)} 条）")
    for e in retrieved:
        print(f"  [{e.id}] {e.title} (score={e.score:.3f})")

    draft = final_state.get("draft", [])
    print_section(f"生成稿（{len(draft)} 条论断）")
    for claim in draft:
        print(f"  [{claim.claim_index}] {claim.text}")
        print(f"      evidence: {', '.join(claim.evidence_ids)}")

    reviews = final_state.get("review_history", [])
    print_section(f"审核结果（{len(reviews)} 条裁决）")
    symbol = {"supported": "[通过]", "partially_supported": "[部分]", "unsupported": "[打回]"}
    for note in reviews:
        print(f"  {symbol.get(note.verdict, '[?]')} [{note.claim_index}] {note.verdict}: {note.reason}")

    rate = hallucination_rate(draft, reviews)
    print(f"\n幻觉率（无溯源支持论断 / 总论断）: {rate:.1%}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(final_state, f, ensure_ascii=False, indent=2, default=lambda o: o.model_dump())
        logger.info("state_written", path=args.out)

    logger.info("cli_done", learner_id=args.learner_id, hallucination_rate=round(rate, 4))


if __name__ == "__main__":
    asyncio.run(main())
