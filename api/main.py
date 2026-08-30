"""FastAPI app 工厂 + 启动时常驻装配。

装配原则（架构文档 §2/§5）：provider / encoder / retriever / teach_graph /
TeachLoop 在 lifespan 内创建一次并常驻（BGE-M3 约 2GB，进程生命周期内只加载
一次）；会话上下文从 DB 重建（TeachLoop.rebuild_context），api 无内存会话态。

降级启动（2026-08-30）：缺向量模型 / 缺 LLM 配置 / 缺数据库时不再直接启动失败，
而是按环境探测结果分级装配：回放/报告等只读端点照常服务，教学链路端点返回 503 +
缺失说明；`GET /api/status` 向前端暴露环境状态（页面展示"缺什么"）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import APIRouter, FastAPI, Request
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


def load_entries(db_path: str, domain: str | None = None) -> list[KnowledgeEntry]:
    """加载知识条目；domain=None 时加载全部域（多域选择模式，按 domain 列分组）。"""
    db = sqlite3.connect(db_path)
    try:
        if domain is not None:
            rows = db.execute(
                "SELECT id, domain, title, content, prerequisites, difficulty, keywords, source, "
                "knowledge_type FROM knowledge_entries WHERE domain=?",
                (domain,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, domain, title, content, prerequisites, difficulty, keywords, source, "
                "knowledge_type FROM knowledge_entries"
            ).fetchall()
    finally:
        db.close()
    return [
        KnowledgeEntry(
            id=r[0],
            title=r[2],
            content=r[3],
            prerequisites=json.loads(r[4] or "[]"),
            difficulty=r[5],
            keywords=json.loads(r[6] or "[]"),
            source=r[7] or "",
            knowledge_type=r[8] or "concept",
        )
        for r in rows
    ]


def _has_sessions_table(db_path: Path) -> bool:
    """数据库就绪判定：文件存在且 sessions 表已建（未初始化的空文件不算就绪）。"""
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        return db.execute("SELECT name FROM sqlite_master WHERE name='sessions'").fetchone() is not None
    finally:
        db.close()


def _has_model_weights(model_dir: Path) -> bool:
    """权重文件检测：safetensors 与 sentence-transformers 的 pytorch_model.bin 两种形态
    都存在——交付包随携模型是后者（2026-08-31 Windows 实测：只认 safetensors 会误报缺模型）。"""
    return any(model_dir.rglob("*.safetensors")) or any(model_dir.rglob("pytorch_model.bin"))


def _is_placeholder(value: str | None) -> bool:
    """.env.example 占位值不算已配置——防无密钥用户拿到"LLM 已接通"的假就绪状态。"""
    return bool(value) and "your" in value.lower()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from dotenv import load_dotenv

    load_dotenv()
    settings = Settings.from_env(require_llm=False)  # 降级启动：不强制 LLM 配置

    # 环境探测（2026-08-30）：缺什么由 /api/status 透出，页面展示缺失项与补救指引；
    # 回放链路只读 DB，不需要模型与 LLM——评委零门槛看回放的前提。
    db_path = Path(settings.database_path)
    db_ready = _has_sessions_table(db_path)
    model_dir = Path(settings.bge_model_path)
    encoder_available = model_dir.exists() and _has_model_weights(model_dir)
    llm_configured = (
        bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)
        and not any(
            _is_placeholder(v)
            for v in (settings.llm_base_url, settings.llm_api_key, settings.llm_model)
        )
    )

    app.state.settings = settings
    store = SessionStore(settings.database_path) if db_ready else None
    app.state.store = store
    app.state.entries = []
    app.state.domains = {}
    app.state.provider = None
    app.state.loop = None

    if db_ready:
        assert store is not None  # db_ready 分支内 store 必非空（窄化类型）
        entries = load_entries(settings.database_path)
        by_domain: dict[str, list[KnowledgeEntry]] = {}
        db = sqlite3.connect(settings.database_path)
        try:
            domains = [r[0] for r in db.execute("SELECT DISTINCT domain FROM knowledge_entries")]
        finally:
            db.close()
        for d in domains:
            by_domain[d] = [e for e in load_entries(settings.database_path, d)]
        app.state.entries = entries
        app.state.domains = by_domain

        if encoder_available and llm_configured:
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
            app.state.provider = provider  # 导出端点直接驱动 distill（不经教学图）
            app.state.loop = TeachLoop(
                graph=build_teach_graph(settings, provider, retriever),
                provider=provider,
                store=store,
                settings=settings,
                entries=entries,
                entries_by_domain=by_domain,
            )
            logger.info("api_ready", mode="full", entries=len(entries), domains=list(by_domain))
        else:
            logger.info(
                "api_ready",
                mode="replay-only",
                entries=len(entries),
                encoder_available=encoder_available,
                llm_configured=llm_configured,
            )
    else:
        logger.warning("api_ready", mode="empty-db")

    # 环境状态（/api/status 数据源）：缺失项措辞面向评委，带补救指引。
    missing: list[str] = []
    if not db_ready:
        missing.append(
            "知识库未初始化：运行 scripts/init_db.py，或将交付包 05-预构建数据库解压到源码根目录"
            "（仅回放也需要此步）"
        )
    if not encoder_available:
        missing.append(
            "未检测到向量模型：将 BGE-M3 放置到 data/bge-m3/"
            "（教学/检索不可用，回放与报告不受影响）"
        )
    if not llm_configured:
        missing.append(
            "未接通 LLM 服务：在 .env 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL"
            "（实时教学不可用，回放不受影响）"
        )
    app.state.status = {
        "db_ready": db_ready,
        "encoder_available": encoder_available,
        "llm_configured": llm_configured,
        "teaching_ready": db_ready and encoder_available and llm_configured,
        "replay_ready": db_ready,
        "domains": [
            {"id": d, "entry_count": len(es)} for d, es in sorted(app.state.domains.items())
        ],
        "missing": missing,
    }
    yield


# 环境状态端点：挂在 /api 前缀（不占用 /api/sessions 路由，避免与 {session_id} 冲突）
status_router = APIRouter(prefix="/api")


@status_router.get("/status")
async def env_status(request: Request):
    """环境状态（降级启动的展示入口）：前端据此提示缺什么、哪些功能可用。"""
    return getattr(request.app.state, "status", {"missing": ["服务装配中"]})


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
    app.include_router(status_router)
    app.include_router(session_routes.router)
    app.include_router(session_routes.resources_router)
    return app


app = create_app()

__all__ = ["app", "create_app", "load_entries"]
