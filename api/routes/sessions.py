"""会话路由：建会话 / 教学流 / 出题 / 作答 / 报告 / 资源 / 回放。

薄层纪律：序列化 → 转发 → 推流；业务全部在 core/teach_loop。
SSE 消费方式：教学端点边执行边推事件；其余端点为普通 JSON。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.models import (
    AnswerRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    PlanTopicOut,
)
from api.sse import sse_frame
from core.mastery import compute_mastery
from core.state import LearnerProfile

logger = structlog.get_logger()

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


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


@router.post("", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest, request: Request):
    _, loop = _deps(request)
    profile = LearnerProfile(
        background=req.background.model_dump(),
        mastery=req.mastery,
        style_tags=req.style_tags,
    )
    try:
        ctx = await loop.start_session(req.learner_id, profile)
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


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    store, _ = _deps(request)
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    return session


@router.post("/{session_id}/teach/{entry_id}")
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


@router.post("/{session_id}/question/{entry_id}")
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
        # 难度匹配：资源包难度层级与诊断层级的匹配率
        "tier_match": {
            "matched": sum(
                1 for p in packages if not str(p["difficulty_tier"]).startswith("capped:")
            ),
            "total": len(packages),
        },
        # 路径图：切片 + 回归回边
        "path": session["plan"].get("topics", []),
        "regressions": [
            e.payload for e in store.load_events(session_id) if e.event_type == "topic_regress"
        ],
    }


@router.get("/{session_id}/resources")
async def resources(session_id: str, request: Request):
    store, _ = _deps(request)
    _session_or_404(store, session_id)
    return store.load_packages(session_id)


@router.get("/{session_id}/replay")
async def replay(session_id: str, request: Request, after_seq: int = 0):
    """回放模式：按 seq 重放历史事件流（评委无 LLM key 环境的演示保障）。"""
    store, _ = _deps(request)
    _session_or_404(store, session_id)

    async def gen() -> AsyncIterator[str]:
        for event in store.load_events(session_id, after_seq=after_seq):
            yield sse_frame(event.event_type, event.payload)

    return StreamingResponse(gen(), media_type="text/event-stream")
