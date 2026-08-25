"""FastAPI app 工厂 + 启动时常驻装配。

装配原则（架构文档 §2/§5）：provider / encoder / retriever / teach_graph /
TeachLoop 在 lifespan 内创建一次并常驻（BGE-M3 约 2GB，进程生命周期内只加载
一次）；会话上下文从 DB 重建（TeachLoop.rebuild_context），api 无内存会话态。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import sessions as session_routes
from core.config import Settings
from core.embedding import BGEEncoder
from core.graph import build_teach_graph
from core.llm import LLMProvider
from core.plan import KnowledgeEntry
from core.retrieval import Retriever
from core.session import SessionStore
from core.teach_loop import TeachLoop

logger = structlog.get_logger()


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from dotenv import load_dotenv

    load_dotenv()
    settings = Settings.from_env()
    base_url, api_key, model = settings.llm_fields()
    provider = LLMProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        extra_body=settings.llm_extra_body,
    )
    logger.info("api_assembling", encoder="bge-m3（~2GB，首次加载约 30s）")
    encoder = BGEEncoder(cache_folder=settings.bge_model_path, local_files_only=True)
    retriever = Retriever(
        db_path=settings.database_path,
        encoder=encoder,
        rrf_k=settings.rrf_k,
        coverage_min_score=settings.coverage_min_score,
    )
    entries = load_entries(settings.database_path)
    if not entries:
        raise RuntimeError("知识库为空，请先运行 scripts/init_db.py")
    app.state.store = SessionStore(settings.database_path)
    app.state.provider = provider  # 导出端点直接驱动 distill（不经教学图）
    app.state.settings = settings
    app.state.loop = TeachLoop(
        graph=build_teach_graph(settings, provider, retriever),
        provider=provider,
        store=app.state.store,
        settings=settings,
        entries=entries,
    )
    app.state.entries = entries
    logger.info("api_ready", entries=len(entries))
    yield


def create_app() -> FastAPI:
    # CORS 来源走 Settings（env 唯一读取点）；此处不要求 LLM 配置——
    # create_app 在 import 期执行，LLM 装配留给 lifespan
    settings = Settings.from_env(require_llm=False)
    app = FastAPI(title="learn-sys", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(session_routes.router)
    app.include_router(session_routes.resources_router)
    return app


app = create_app()

__all__ = ["app", "create_app", "load_entries"]
