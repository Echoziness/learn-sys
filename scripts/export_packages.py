#!/usr/bin/env python3
"""资源包条目化导出：会话沉淀的资源包 → entries.jsonl 同构条目（产出物可复用）。

用法：
  uv run python scripts/export_packages.py                  # 最新已完成会话
  uv run python scripts/export_packages.py --session <id>   # 指定会话
  uv run python scripts/export_packages.py --out <path>     # 自定义输出

产出物与知识库同一规范（SeedEntry schema）：导出文件可被 init_db 原样入库
——"产出物可复用"的硬证明。进库的是知识本身：讲义论断（审核通过）直接
拼接；错题/脚手架原料经 distill agent 提炼为"常见误区"知识段落进入 content。

自检通过后双写：entries.jsonl 文件（交付物）+ exported_entries 表
（GET /api/sessions/{id}/exports 数据源，报告页展示条目本体）。

自检：每条过 SeedEntry 校验 + 关键词字符 ⊆ content（判分同语义）+ id 唯一，
任一失败非零退出（导出物必须天然满足知识库约束，不靠事后修）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

import structlog
from scripts.init_db import SeedEntry

from core.agents.distill import distill_pitfalls
from core.config import Settings
from core.deliver import package_to_entry
from core.llm import LLMProvider
from core.plan import KnowledgeEntry
from core.session import SessionStore

logger = structlog.get_logger()


def load_entries(db_path: str, domain: str = "bigdata-analysis") -> list[KnowledgeEntry]:
    """从 DB 重建知识条目（与 evals/run.py 同口径——脚本层各自重建，core 无 DB 依赖）。"""
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


def pick_session(store: SessionStore, session_id: str | None) -> dict[str, Any]:
    """指定 id 或取最新 finished 会话（导出以完整会话为默认）。"""
    if session_id:
        s = store.get_session(session_id)
        if s is None:
            raise SystemExit(f"会话不存在: {session_id}")
        return s
    for s in store.list_sessions(limit=50):
        if s["status"] == "finished":
            return store.get_session(s["session_id"])  # type: ignore[return-value]
    raise SystemExit("没有已完成会话——先跑完一个会话再导出")


def claims_total_by_entry(store: SessionStore, session_id: str) -> dict[str, int]:
    """每条目全部轮次的论断总数（审核通过率的分母，源自 teach_delivered 事件）。"""
    totals: dict[str, int] = {}
    for ev in store.load_events(session_id, limit=2000):
        if ev.event_type != "teach_delivered":
            continue
        eid = ev.payload.get("entry_id", "")
        totals[eid] = totals.get(eid, 0) + len(ev.payload.get("claims", []))
    return totals


def collect_fail_material(
    store: SessionStore, session_id: str, entry_id: str
) -> tuple[list[dict[str, str]], list[str]]:
    """distill 原料：答错记录（题目/错答/评估/遗漏）+ 脚手架干扰项（镜像错误理解）。"""
    wrong_records: list[dict[str, str]] = []
    scaffold_distractors: list[str] = []
    for r in store.load_rounds(session_id, entry_id):
        q = r.get("question") or {}
        grade = r.get("grade") or {}
        if r.get("answer") is not None and not grade.get("is_correct", False):
            wrong_records.append(
                {
                    "prompt": q.get("prompt", ""),
                    "answer": r["answer"],
                    "evaluation": grade.get("evaluation", ""),
                    "missed": "；".join(grade.get("missed_requirements") or []),
                }
            )
        if q.get("question_id", "").endswith("_scaffold"):
            label = q.get("expected_label", "A")
            for opt in q.get("options", []):
                if not opt.startswith(f"{label}."):
                    scaffold_distractors.append(opt)
    return wrong_records[:5], scaffold_distractors[:6]  # 原料截断：误区提炼只需代表样本


def validate_exported(entries: list[dict[str, Any]]) -> list[str]:
    """同构自检：SeedEntry 校验 + 关键词字符 ⊆ content + id 唯一。"""
    errors: list[str] = []
    seen: set[str] = set()
    for raw in entries:
        try:
            e = SeedEntry.model_validate(raw)
        except Exception as exc:
            errors.append(f"{raw.get('id', '?')} schema 校验失败: {str(exc)[:120]}")
            continue
        if e.id in seen:
            errors.append(f"{e.id} id 重复")
        seen.add(e.id)
        chars = set(re.sub(r"\s+", "", e.content.lower()))
        for kw in e.keywords:
            if not set(re.sub(r"\s+", "", kw.lower())) <= chars:
                errors.append(f"{e.id} 关键词 {kw!r} 字符未全部出现在 content")
    return errors


def persist_exported(
    store: SessionStore, session_id: str, learner_id: str, exported: list[dict[str, Any]]
) -> None:
    """导出产物落库（GET /exports 数据源）。源条目 id 由生成 id 规范反推。"""
    rows = [
        {
            **item,
            "source_entry_id": item["id"].removeprefix("GEN-").removesuffix(f"-{learner_id}"),
        }
        for item in exported
    ]
    store.save_export_entries(session_id, rows)


async def collect_export_entries(
    store: SessionStore,
    session: dict[str, Any],
    entries: list[KnowledgeEntry],
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """导出主流程（可测）：逐包 收集错题素材 → distill 提炼 → 条目化。"""
    sid: str = session["session_id"]
    level: str = session.get("difficulty_level") or "beginner"
    packages = store.load_packages(sid)
    totals = claims_total_by_entry(store, sid)
    by_id = {e.id: e for e in entries}

    exported: list[dict[str, Any]] = []
    for pkg in packages:
        source = by_id.get(pkg["entry_id"])
        if source is None:
            logger.warning("export_skip_unknown_entry", entry_id=pkg["entry_id"])
            continue
        wrong, distractors = collect_fail_material(store, sid, pkg["entry_id"])
        pitfalls = await distill_pitfalls(
            {
                "entry": {"id": source.id, "title": source.title, "content": source.content},
                "wrong_records": wrong,
                "scaffold_distractors": distractors,
            },
            provider=provider,
            model=model,
        )
        item = package_to_entry(
            pkg,
            source,
            learner_id=session["learner_id"],
            difficulty_level=level,
            claims_total=totals.get(pkg["entry_id"], 0),
            pitfalls=pitfalls,
        )
        if item is None:
            logger.warning("export_skip_empty_lecture", entry_id=pkg["entry_id"])
            continue
        exported.append(item)
        logger.info(
            "export_entry",
            id=item["id"], content_len=len(item["content"]),
            keywords=len(item["keywords"]), pitfalls=len(pitfalls),
        )
    return exported


async def main() -> None:
    parser = argparse.ArgumentParser(description="资源包条目化导出")
    parser.add_argument("--session", default=None, help="会话 id（默认最新 finished）")
    parser.add_argument("--out", default=None, help="输出路径（默认 data/exports/）")
    parser.add_argument("--domain", default="bigdata-analysis", help="源知识库领域")
    args = parser.parse_args()

    settings = Settings.from_env()
    base_url, api_key, model = settings.llm_fields()
    provider = LLMProvider(
        base_url=base_url, api_key=api_key, model=model,
        extra_body=settings.llm_extra_body,
    )
    store = SessionStore(settings.database_path)
    entries = load_entries(settings.database_path, args.domain)

    session = pick_session(store, args.session)
    if not store.load_packages(session["session_id"]):
        raise SystemExit(f"会话 {session['session_id'][:8]} 无资源包可导出")
    exported = await collect_export_entries(
        store, session, entries, provider=provider, model=settings.generate_model
    )

    errors = validate_exported(exported)
    if errors:
        for e in errors:
            print(f"[自检失败] {e}", file=sys.stderr)
        raise SystemExit(1)

    sid = session["session_id"]
    persist_exported(store, sid, session["learner_id"], exported)
    await store.emit(
        sid,
        "packages_exported",
        {"entry_ids": [item["id"] for item in exported], "count": len(exported)},
    )

    out = Path(args.out) if args.out else Path(
        f"data/exports/entries-{session['learner_id']}-{session['session_id'][:8]}.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for item in exported:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"导出 {len(exported)} 条 → {out}（自检通过，可被 init_db 原样入库）")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    from core.logging import configure_logging

    configure_logging()
    asyncio.run(main())
