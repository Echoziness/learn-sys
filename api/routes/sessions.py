"""会话路由：建会话 / 教学流 / 出题 / 作答 / 报告 / 资源 / 回放。

薄层纪律：序列化 → 转发 → 推流；业务全部在 core/teach_loop。
SSE 消费方式：教学端点边执行边推事件；其余端点为普通 JSON。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from api.models import (
    AggregatedExportEntryOut,
    AggregatedPackageOut,
    AnswerRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    DeleteSessionOut,
    DomainOut,
    ExportedEntryOut,
    FollowupAskOut,
    FollowupRequest,
    PlanTopicOut,
    ResourcesAggregateOut,
    SessionListItemOut,
)
from api.sse import sse_frame
from core.export_pipeline import ExportValidationError, export_to_jsonl, run_export
from core.mastery import compute_mastery
from core.state import DraftClaim, LearnerProfile, ReviewNote
from evals.metrics import hallucination_rate, keyword_coverage, tier_match_rate

logger = structlog.get_logger()

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# 跨会话聚合端点（资源库页面）：不属于单个会话，独立前缀
resources_router = APIRouter(prefix="/api", tags=["resources"])


def _deps(request: Request):
    """进程级装配依赖（lifespan 注入）。"""
    store = getattr(request.app.state, "store", None)
    loop = getattr(request.app.state, "loop", None)
    if store is None or loop is None:
        raise HTTPException(503, "服务装配中，请稍候重试")
    return store, loop


def _session_or_404(store, session_id: str) -> None:
    if store.get_session(session_id) is None:
        raise HTTPException(404, "会话不存在")


@router.get("/domains", response_model=list[DomainOut])
async def list_domains(request: Request):
    """可选教学领域（seeds 子目录）：建会话时自选领域——换目录即换领域的入口。"""
    by_domain = getattr(request.app.state, "domains", {})
    return [
        DomainOut(id=d, entry_count=len(entries))
        for d, entries in sorted(by_domain.items())
    ]


@router.post("", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest, request: Request):
    """建会话：画像 → 诊断 + 切片 → 持久化。domain 选定教学领域（默认大数据分析）。"""
    _, loop = _deps(request)
    profile = LearnerProfile(
        background=req.background.model_dump(),
        mastery=req.mastery,
        style_tags=req.style_tags,
    )
    try:
        ctx = await loop.start_session(req.learner_id, profile, domain=req.domain)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"诊断失败: {str(exc)[:200]}") from exc
    return CreateSessionResponse(
        session_id=ctx.session_id,
        learner_id=ctx.learner_id,
        difficulty_level=ctx.difficulty_level,
        profile_summary=ctx.profile_summary,
        gap_ids=ctx.gap_ids,
        topics=[
            PlanTopicOut(entry_id=t.entry_id, title=t.title, order=t.order, target=t.target)
            for t in ctx.plan.topics
        ],
        uncovered_gaps=ctx.plan.uncovered_gaps,
    )


@router.get("", response_model=list[SessionListItemOut])
async def list_sessions(request: Request, limit: int = 100):
    """历史会话列表（回放入口页）。"""
    store, _ = _deps(request)
    return store.list_sessions(limit=min(limit, 500))


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    store, _ = _deps(request)
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    return session


@router.post("/{session_id}/topics/{entry_id}/teach")
async def teach(session_id: str, entry_id: str, request: Request):
    """执行一轮教学：SSE 推送 topic_start → retrieve/generate/review → teach_delivered。"""
    store, loop = _deps(request)
    _session_or_404(store, session_id)
    ctx = loop.rebuild_context(session_id)
    queue = store.subscribe(session_id)

    async def run() -> AsyncIterator[str]:
        task = asyncio.create_task(loop.teach_round(ctx, entry_id))
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=180.0)
                yield sse_frame(event.event_type, event.payload)
                if event.event_type == "teach_delivered":
                    break
        except TimeoutError:
            yield sse_frame("error", {"stage": "teach", "message": "教学执行超时"})
        except Exception as exc:  # noqa: BLE001 —— SSE 不静默断流，错误以事件透出
            yield sse_frame("error", {"stage": "teach", "message": str(exc)[:200]})
        finally:
            store.unsubscribe(session_id, queue)
            if not task.done():
                await task

    return StreamingResponse(run(), media_type="text/event-stream")


@router.post("/{session_id}/topics/{entry_id}/question")
async def question(session_id: str, entry_id: str, request: Request):
    store, loop = _deps(request)
    _session_or_404(store, session_id)
    ctx = loop.rebuild_context(session_id)
    try:
        q = await loop.next_question(ctx, entry_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"出题失败: {str(exc)[:200]}") from exc
    return {
        "question_id": q.question_id,
        "entry_id": q.entry_id,
        "question_type": q.question_type,
        "prompt": q.prompt,
        "options": list(q.options),
    }


@router.post("/{session_id}/answers")
async def answer(session_id: str, req: AnswerRequest, request: Request):
    store, loop = _deps(request)
    _session_or_404(store, session_id)
    pending = store.get_pending_round(session_id, req.entry_id)
    if pending is None or pending.get("question") is None:
        raise HTTPException(409, "当前无待作答题目，请先出题")
    ctx = loop.rebuild_context(session_id)
    q = loop.question_from_record(pending)
    try:
        result = await loop.handle_answer(ctx, req.entry_id, q, req.answer)
    except Exception as exc:
        raise HTTPException(502, f"判分失败: {str(exc)[:200]}") from exc
    return {
        "is_correct": result.outcome.is_correct,
        "coverage": round(result.outcome.grade.keyword_coverage, 3),
        "evaluation": result.outcome.evaluation,
        "missed_requirements": list(result.outcome.missed_requirements),
        "decision": result.decision,
        "mastery": round(result.mastery, 3),
        "round_no": result.round_no,
        "is_scaffold": result.is_scaffold,
    }


@router.post("/{session_id}/topics/{entry_id}/followup", response_model=FollowupAskOut)
async def followup_ask(
    session_id: str, entry_id: str, req: FollowupRequest, request: Request
):
    """动态追问：判定学生提问是否真实有效 → 有效则记录困惑并直接给出解答（不即时出题）。"""
    store, loop = _deps(request)
    _session_or_404(store, session_id)
    ctx = loop.rebuild_context(session_id)
    try:
        result = await loop.handle_followup(ctx, entry_id, req.question)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"追问判定失败: {str(exc)[:200]}") from exc
    return FollowupAskOut(
        valid=result.valid,
        reason=result.reason,
        round_no=result.round_no,
        answer=result.answer or "",
    )


@router.get("/{session_id}/report")
async def report(session_id: str, request: Request):
    store, loop = _deps(request)
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    entries = request.app.state.entries
    title_by_id = {e.id: e.title for e in entries}

    by_entry: dict[str, list[bool]] = {}
    for snap in store.load_mastery_report(session_id):
        by_entry.setdefault(snap["entry_id"], []).append(snap["correctness"])
    packages = store.load_packages(session_id)
    events = store.load_events(session_id)

    # 三指标口径全部走 evals/metrics.py SSOT（与批量评测 evals/run.py 同源）：
    # 幻觉率从 teach_delivered 事件的 claims+verdicts 聚合（最终轮裁决）——
    # claim_index 是事件内局部编号，聚合时 claims 与 verdicts 必须同步偏移。
    drafts: list[DraftClaim] = []
    reviews: list[ReviewNote] = []
    for ev in events:
        if ev.event_type != "teach_delivered":
            continue
        base = len(drafts)
        for c in ev.payload.get("claims", []):
            drafts.append(
                DraftClaim(
                    claim_index=base + int(c["claim_index"]),
                    text=c["text"],
                    evidence_ids=c.get("evidence_ids", ["?"]),
                    claim_type=c.get("claim_type", "core"),
                )
            )
        for i, verdict in ev.payload.get("verdicts", {}).items():
            reviews.append(ReviewNote(claim_index=base + int(i), verdict=verdict, reason="报告聚合"))
    keywords = {e.id: e.keywords for e in entries if e.id in {p["entry_id"] for p in packages}}
    tier_rate, tier_matched, tier_total = tier_match_rate(packages)
    cov_rate, cov_hit, cov_total = keyword_coverage(packages, keywords)
    return {
        "session_id": session_id,
        "difficulty_level": session["difficulty_level"],
        # 盲区雷达：逐条目掌握度
        "radar": [
            {
                "entry_id": eid,
                "title": title_by_id.get(eid, eid),
                "mastery": round(compute_mastery(history), 3),
                "attempts": len(history),
            }
            for eid, history in by_entry.items()
        ],
        # 难度匹配：资源包难度层级与诊断层级（容忍带内非 capped 即适配）
        "tier_match": {"matched": tier_matched, "total": tier_total},
        # 逐包层级明细（报告页徽章展示用）
        "tiers": [
            {
                "entry_id": p["entry_id"],
                "title": title_by_id.get(p["entry_id"], p["entry_id"]),
                "tier": str(p["difficulty_tier"]),
                "matched": not str(p["difficulty_tier"]).startswith("capped:"),
            }
            for p in packages
        ],
        # 赛题三指标总览（与 evals/run.py 逐组结果同口径）
        "metrics": {
            "hallucination_rate": round(hallucination_rate(drafts, reviews), 4),
            "claims_total": len(drafts),
            "tier_match": {"rate": round(tier_rate, 4), "matched": tier_matched, "total": tier_total},
            "keyword_coverage": {"rate": round(cov_rate, 4), "hit": cov_hit, "total": cov_total},
        },
        # 路径图：切片 + 回归回边——同一主题可多次回退（重教后又答错再退），
        # 按 (entry_id, prereq_id) 去重：路径图画的是发生过哪些回退，重复回退只画一条边，
        # 重复记录会让前端边 key 碰撞（regress-<entry_id>）触发 React duplicate key
        "path": session["plan"].get("topics", []),
        "regressions": list(
            {
                (e.payload.get("entry_id"), e.payload.get("prereq_id")): e.payload
                for e in events
                if e.event_type == "topic_regress"
            }.values()
        ),
    }


@router.get("/{session_id}/resources")
async def resources(session_id: str, request: Request):
    store, _ = _deps(request)
    _session_or_404(store, session_id)
    return store.load_packages(session_id)


@router.get("/{session_id}/exports", response_model=list[ExportedEntryOut])
async def exports(session_id: str, request: Request):
    """条目化导出产物（知识库同构条目，FR-23）——由导出管线产出后落库。"""
    store, _ = _deps(request)
    _session_or_404(store, session_id)
    return store.load_export_entries(session_id)


@router.post("/{session_id}/export")
async def export_packages(session_id: str, request: Request):
    """主动触发资源包条目化导出（Web 入口）：收集 → distill 提炼 → 自检 → 落库 → 事件。"""
    store, _ = _deps(request)
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    provider = getattr(request.app.state, "provider", None)
    settings = getattr(request.app.state, "settings", None)
    if provider is None or settings is None:
        raise HTTPException(503, "服务装配中，请稍候重试")
    if not store.load_packages(session_id):
        raise HTTPException(409, "会话暂无资源包可导出（请先完成至少一个主题的教学）")
    try:
        exported = await run_export(
            store,
            session,
            request.app.state.entries,
            provider=provider,
            model=settings.generate_model,
        )
    except ExportValidationError as exc:
        raise HTTPException(502, f"导出自检失败: {'; '.join(exc.errors)[:300]}") from exc
    except Exception as exc:
        raise HTTPException(502, f"导出失败: {str(exc)[:200]}") from exc
    logger.info("export_triggered", session_id=session_id, count=len(exported))
    return {
        "session_id": session_id,
        "count": len(exported),
        "entry_ids": [item["id"] for item in exported],
    }


@router.get("/{session_id}/export/download")
async def export_download(session_id: str, request: Request):
    """下载导出文件（entries.jsonl 同构，可被 init_db 原样入库）。

    会话已删但产物保留（keep_exports）时仍可下载——资源库页孤儿来源依赖此行为。
    """
    store, _ = _deps(request)
    stored = store.load_export_entries(session_id)
    if not stored:
        raise HTTPException(409, "尚无导出条目，请先触发导出")
    # 落库形态含溯源/时间戳字段，下载文件只留 SeedEntry 同构字段（与 CLI 导出同构）
    items = [
        {k: v for k, v in e.items() if k not in ("source_entry_id", "exported_at")}
        for e in stored
    ]
    session = store.get_session(session_id)
    learner = session["learner_id"] if session else "deleted"
    filename = f"entries-{learner}-{session_id[:8]}.jsonl"
    return PlainTextResponse(
        export_to_jsonl(items),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{session_id}", response_model=DeleteSessionOut)
async def delete_session(
    session_id: str,
    request: Request,
    keep_packages: bool = False,
    keep_exports: bool = False,
):
    """删除会话与过程数据（事件/轮次/掌握度）；资源包与导出条目可按参数额外保留。"""
    store, _ = _deps(request)
    _session_or_404(store, session_id)
    deleted = store.delete_session(
        session_id, keep_packages=keep_packages, keep_exports=keep_exports
    )
    logger.info(
        "session_deleted",
        session_id=session_id,
        keep_packages=keep_packages,
        keep_exports=keep_exports,
        deleted=deleted,
    )
    return DeleteSessionOut(
        session_id=session_id,
        deleted=deleted,
        kept_packages=keep_packages,
        kept_exports=keep_exports,
    )


@resources_router.get("/resources", response_model=ResourcesAggregateOut)
async def all_resources(
    request: Request, session_id: str | None = None, entry_id: str | None = None
):
    """跨会话聚合资源包与导出条目（资源库页面数据源），可按来源会话/条目筛选。"""
    store, _ = _deps(request)
    return ResourcesAggregateOut(
        packages=[AggregatedPackageOut(**p) for p in store.load_all_packages(
            session_id=session_id, entry_id=entry_id
        )],
        exports=[AggregatedExportEntryOut(**e) for e in store.load_all_export_entries(
            session_id=session_id, entry_id=entry_id
        )],
    )


@router.get("/{session_id}/replay")
async def replay(session_id: str, request: Request, after_seq: int = 0, format: str = "sse"):
    """回放模式：按 seq 重放历史事件流（评委无 LLM key 环境的演示保障）。

    format=json 返回带 seq 的数组（前端播放器步进/进度条用）；默认 SSE 流。
    """
    store, _ = _deps(request)
    _session_or_404(store, session_id)

    if format == "json":
        return [
            {
                "seq": e.seq,
                "event_type": e.event_type,
                "payload": e.payload,
                "created_at": e.created_at,
            }
            for e in store.load_events(session_id, after_seq=after_seq)
        ]

    async def gen() -> AsyncIterator[str]:
        for event in store.load_events(session_id, after_seq=after_seq):
            yield sse_frame(event.event_type, event.payload)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/{session_id}/stream")
async def stream(session_id: str, request: Request, after_seq: int = 0):
    """实时事件订阅（裁判面跟随另一个 tab 的会话进度）。

    顺序保证：先 subscribe 再补读历史——两者重叠的事件按 seq 去重。
    session_end / error 后自然收流；其余情况保持连接（keep-alive 注释帧）。
    """
    store, _ = _deps(request)
    _session_or_404(store, session_id)
    queue = store.subscribe(session_id)

    async def gen() -> AsyncIterator[str]:
        last_seq = after_seq
        try:
            for event in store.load_events(session_id, after_seq=last_seq):
                last_seq = event.seq
                yield sse_frame(event.event_type, event.payload)
                if event.event_type in ("session_end", "error"):
                    return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event.seq <= last_seq:  # 补读与订阅的重叠
                    continue
                last_seq = event.seq
                yield sse_frame(event.event_type, event.payload)
                if event.event_type in ("session_end", "error"):
                    return
        finally:
            store.unsubscribe(session_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{session_id}/end")
async def end_session(session_id: str, request: Request, status: str = "finished"):
    """结束会话：发 session_end 汇总事件并落状态（报告页前置动作）。"""
    store, loop = _deps(request)
    _session_or_404(store, session_id)
    ctx = loop.rebuild_context(session_id)
    await loop.end_session(ctx, status=status if status in ("finished", "aborted") else "finished")
    return {"session_id": session_id, "status": status}


@router.get("/{session_id}/topics/{entry_id}/state")
async def topic_state(session_id: str, entry_id: str, request: Request):
    """主题进度状态（学生面驱动循环的依据：巩固模式跳过教学 / regress 跳转目标）。

    全部从 DB 历史推导（D2），web 刷新后调用即可恢复交互位置。
    """
    store, loop = _deps(request)
    _session_or_404(store, session_id)
    ctx = loop.rebuild_context(session_id)
    progress = loop.progress(session_id, entry_id)
    try:
        entry = ctx.entry(entry_id)
        prereq_id = entry.prerequisites[0] if entry.prerequisites else None
        title = entry.title
    except KeyError:
        prereq_id = None
        title = entry_id
    # 最近一条困惑记录（刷新恢复：前端据此重绘追问区）+ 未消化困惑数（驱动下轮教学）
    followup_last = loop.last_followup_record(session_id, entry_id)
    pending_followups = loop.pending_followups(session_id, entry_id)
    return {
        "entry_id": entry_id,
        "title": title,
        # 有未消化困惑时强制教学（巩固模式让位——困惑需要针对性讲解）
        "needs_teaching": progress.needs_teaching or bool(pending_followups),
        "next_round_no": progress.next_round_no,
        "scaffold_pending": progress.scaffold_pending,
        "prereq_id": prereq_id,
        "has_answered": progress.last_round is not None,
        "followup_last": followup_last,
        "pending_followup_count": len(pending_followups),
    }
