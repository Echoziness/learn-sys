#!/usr/bin/env python3
"""资源包条目化导出 CLI：会话沉淀的资源包 → entries.jsonl 同构条目（产出物可复用）。

用法：
  uv run python scripts/export_packages.py                  # 最新已完成会话
  uv run python scripts/export_packages.py --session <id>   # 指定会话
  uv run python scripts/export_packages.py --out <path>     # 自定义输出

管线本体在 core/export_pipeline.py（CLI 与 Web 端点共用）；本脚本是组合根薄壳：
选会话 → 建 provider → 跑管线 → 双写 entries.jsonl 文件（交付物）+ exported_entries 表
（GET /api/sessions/{id}/exports 数据源，报告页展示条目本体）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

import structlog

from core.config import Settings

# 管线函数下沉 core（Web 导出端点共用）；此处 re-export 保持既有导入路径兼容
from core.export_pipeline import (  # noqa: F401
    ExportValidationError,
    claims_total_by_entry,
    collect_export_entries,
    collect_fail_material,
    export_to_jsonl,
    persist_exported,
    run_export,
    validate_exported,
)
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
    try:
        exported = await run_export(
            store, session, entries, provider=provider, model=settings.generate_model
        )
    except ExportValidationError as exc:
        for e in exc.errors:
            print(f"[自检失败] {e}", file=sys.stderr)
        raise SystemExit(1) from exc

    out = Path(args.out) if args.out else Path(
        f"data/exports/entries-{session['learner_id']}-{session['session_id'][:8]}.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(export_to_jsonl(exported))
    print(f"导出 {len(exported)} 条 → {out}（自检通过，可被 init_db 原样入库）")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    from core.logging import configure_logging

    configure_logging()
    asyncio.run(main())
