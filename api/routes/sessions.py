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
    SessionListItemOut,
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
    return {
        "entry_id": entry_id,
        "title": title,
        "needs_teaching": progress.needs_teaching,
        "next_round_no": progress.next_round_no,
        "scaffold_pending": progress.scaffold_pending,
        "prereq_id": prereq_id,
        "has_answered": progress.last_round is not None,
    }
