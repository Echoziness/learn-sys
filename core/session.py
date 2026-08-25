"""SessionStore：会话/事件流/轮次/掌握度/资源包的 DB 读写 + 事件发射（W1）。

设计（架构文档 §3.2/§4）：
- 事件流一表三用：裁判面渲染协议 / 回放媒体流 / 审计日志；
- emit = 写库（seq 会话内单调）+ 进程内订阅推送（SSE 消费）；回放直接读表；
- payload 自包含：前端仅凭 payload 可渲染，无需回查其他表；
- 无模块级副作用：连接由组合根创建注入；每个方法短事务，WAL 下并发安全。

进度推导约定（D2）：主题的题型推进状态（reached_answer / scaffold_pending /
retry 上下文）一律从 topic_rounds 历史推导，不另立状态字段——api 无内存会话态，
任何进程重启后从 DB 即可重建会话。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SessionEvent:
    """一条会话事件（协议见架构文档 §4）。"""

    session_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


class SessionStore:
    """会话持久化。一个实例服务一个 DB 文件；线程/协程共享安全（每方法独立短事务）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = {}
        self._seq_lock = asyncio.Lock()  # seq 分配与订阅推送的临界区

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    # ---------- 会话 ----------

    def create_session(
        self,
        learner_id: str,
        profile: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> str:
        sid = session_id or uuid.uuid4().hex
        db = self._connect()
        try:
            db.execute(
                "INSERT INTO sessions(session_id, learner_id, profile_json, status, created_at) "
                "VALUES (?, ?, ?, 'active', ?)",
                (sid, learner_id, json.dumps(profile, ensure_ascii=False), _now()),
            )
            db.commit()
        finally:
            db.close()
        return sid

    def save_diagnosis(
        self,
        session_id: str,
        *,
        gap_ids: list[str],
        difficulty_level: str,
        profile_summary: str,
        plan: dict[str, Any],
    ) -> None:
        db = self._connect()
        try:
            db.execute(
                """UPDATE sessions SET gap_ids_json=?, difficulty_level=?,
                   profile_summary=?, plan_json=? WHERE session_id=?""",
                (
                    json.dumps(gap_ids, ensure_ascii=False),
                    difficulty_level,
                    profile_summary,
                    json.dumps(plan, ensure_ascii=False),
                    session_id,
                ),
            )
            db.commit()
        finally:
            db.close()

    def finish_session(self, session_id: str, *, status: str = "finished") -> None:
        db = self._connect()
        try:
            db.execute(
                "UPDATE sessions SET status=?, finished_at=? WHERE session_id=?",
                (status, _now(), session_id),
            )
            db.commit()
        finally:
            db.close()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT session_id, learner_id, profile_json, gap_ids_json, difficulty_level, "
                "profile_summary, plan_json, status, created_at, finished_at "
                "FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        return {
            "session_id": row[0],
            "learner_id": row[1],
            "profile": json.loads(row[2]),
            "gap_ids": json.loads(row[3]) if row[3] else [],
            "difficulty_level": row[4],
            "profile_summary": row[5],
            "plan": json.loads(row[6]) if row[6] else {},
            "status": row[7],
            "created_at": row[8],
            "finished_at": row[9],
        }

    def list_sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """会话列表（回放入口页）。含事件数/资源包数/规划主题数，供列表展示与筛选。"""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT s.session_id, s.learner_id, s.difficulty_level, s.status, "
                "s.created_at, s.finished_at, s.plan_json, "
                "(SELECT COUNT(*) FROM session_events e WHERE e.session_id = s.session_id), "
                "(SELECT COUNT(*) FROM resource_packages p WHERE p.session_id = s.session_id) "
                "FROM sessions s ORDER BY s.created_at DESC, s.rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "session_id": r[0],
                "learner_id": r[1],
                "difficulty_level": r[2],
                "status": r[3],
                "created_at": r[4],
                "finished_at": r[5],
                "topic_count": len((json.loads(r[6]) or {}).get("topics", [])) if r[6] else 0,
                "event_count": r[7],
                "package_count": r[8],
            }
            for r in rows
        ]

    # ---------- 事件流 ----------

    async def emit(self, session_id: str, event_type: str, payload: dict[str, Any]) -> SessionEvent:
        """写库（seq 单调）+ 推送给订阅者。SSE 端点与回放共用此协议。"""
        async with self._seq_lock:
            db = self._connect()
            try:
                row = db.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM session_events WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                seq = int(row[0])
                created = _now()
                db.execute(
                    "INSERT INTO session_events(session_id, seq, event_type, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, seq, event_type, json.dumps(payload, ensure_ascii=False), created),
                )
                db.commit()
            finally:
                db.close()
            event = SessionEvent(session_id, seq, event_type, payload, created)
            for queue in self._subscribers.get(session_id, set()):
                queue.put_nowait(event)
            return event

    def subscribe(self, session_id: str) -> asyncio.Queue[SessionEvent]:
        """注册 SSE 订阅。队列从当前时刻开始接收；历史事件用 load_events 读。"""
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[SessionEvent]) -> None:
        queues = self._subscribers.get(session_id)
        if queues:
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(session_id, None)

    def load_events(
        self, session_id: str, *, after_seq: int = 0, limit: int = 1000
    ) -> list[SessionEvent]:
        """回放/补发：按 seq 升序读取事件（after_seq 之后）。"""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT session_id, seq, event_type, payload_json, created_at "
                "FROM session_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
                (session_id, after_seq, limit),
            ).fetchall()
        finally:
            db.close()
        return [
            SessionEvent(r[0], r[1], r[2], json.loads(r[3]), r[4]) for r in rows
        ]

    # ---------- 教学轮 ----------

    def save_round(
        self,
        session_id: str,
        *,
        entry_id: str,
        round_no: int,
        question: dict[str, Any] | None,
        expected: list[str] | None,
        answer: str | None,
        grade: dict[str, Any] | None,
        decision: str,
        mastery_after: float | None,
    ) -> None:
        db = self._connect()
        try:
            db.execute(
                """INSERT INTO topic_rounds
                   (session_id, entry_id, round_no, question_json, expected_json,
                    answer_text, grade_json, decision, mastery_after, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    entry_id,
                    round_no,
                    json.dumps(question, ensure_ascii=False) if question else None,
                    json.dumps(expected, ensure_ascii=False) if expected is not None else None,
                    answer,
                    json.dumps(grade, ensure_ascii=False) if grade else None,
                    decision,
                    mastery_after,
                    _now(),
                ),
            )
            db.commit()
        finally:
            db.close()

    def get_pending_round(self, session_id: str, entry_id: str) -> dict[str, Any] | None:
        """最近一条未作答的主教学轮（next_question 幂等复用：web 刷新安全）。

        decision='pending' 过滤：追问侧车轮（decision='followup'）不占用主出题通道。
        """
        db = self._connect()
        try:
            row = db.execute(
                "SELECT session_id, entry_id, round_no, question_json, expected_json, "
                "answer_text, grade_json, decision, mastery_after, created_at "
                "FROM topic_rounds "
                "WHERE session_id=? AND entry_id=? AND answer_text IS NULL AND decision='pending' "
                "ORDER BY id DESC LIMIT 1",
                (session_id, entry_id),
            ).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        return {
            "session_id": row[0],
            "entry_id": row[1],
            "round_no": row[2],
            "question": json.loads(row[3]) if row[3] else None,
            "expected": json.loads(row[4]) if row[4] else None,
            "answer": row[5],
            "grade": json.loads(row[6]) if row[6] else None,
            "decision": row[7],
            "mastery_after": row[8],
            "created_at": row[9],
        }

    def update_round_answer(
        self,
        session_id: str,
        entry_id: str,
        round_no: int,
        *,
        answer: str,
        grade: dict[str, Any],
        decision: str,
        mastery_after: float,
    ) -> None:
        """作答落地：填充 pending 轮（answer/grade/decision/mastery）。"""
        db = self._connect()
        try:
            db.execute(
                """UPDATE topic_rounds SET answer_text=?, grade_json=?, decision=?, mastery_after=?
                   WHERE session_id=? AND entry_id=? AND round_no=? AND answer_text IS NULL""",
                (
                    answer,
                    json.dumps(grade, ensure_ascii=False),
                    decision,
                    mastery_after,
                    session_id,
                    entry_id,
                    round_no,
                ),
            )
            db.commit()
        finally:
            db.close()

    def delete_pending_rounds(self, session_id: str, entry_id: str) -> None:
        """重教作废未作答的轮（teach_round 前置清理）——未作答无审计价值，事件流留痕。

        含未作答的追问侧车轮（重教后教学内容更新，旧确认题基于旧论断失效）。
        """
        db = self._connect()
        try:
            db.execute(
                "DELETE FROM topic_rounds "
                "WHERE session_id=? AND entry_id=? AND answer_text IS NULL",
                (session_id, entry_id),
            )
            db.commit()
        finally:
            db.close()

    def delete_pending_followup(self, session_id: str, entry_id: str) -> None:
        """作废未作答的追问侧车轮（新提问替换旧确认题）。

        只删 decision='followup' 的未作答行，不碰主教学轮的 pending 题目。
        """
        db = self._connect()
        try:
            db.execute(
                "DELETE FROM topic_rounds "
                "WHERE session_id=? AND entry_id=? AND answer_text IS NULL AND decision='followup'",
                (session_id, entry_id),
            )
            db.commit()
        finally:
            db.close()

    def load_rounds(self, session_id: str, entry_id: str | None = None) -> list[dict[str, Any]]:
        """教学轮历史（seq 即轮次写入序）。进度推导（D2）的数据源。"""
        db = self._connect()
        try:
            if entry_id:
                rows = db.execute(
                    "SELECT session_id, entry_id, round_no, question_json, expected_json, "
                    "answer_text, grade_json, decision, mastery_after, created_at "
                    "FROM topic_rounds WHERE session_id=? AND entry_id=? ORDER BY id",
                    (session_id, entry_id),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT session_id, entry_id, round_no, question_json, expected_json, "
                    "answer_text, grade_json, decision, mastery_after, created_at "
                    "FROM topic_rounds WHERE session_id=? ORDER BY id",
                    (session_id,),
                ).fetchall()
        finally:
            db.close()
        return [
            {
                "session_id": r[0],
                "entry_id": r[1],
                "round_no": r[2],
                "question": json.loads(r[3]) if r[3] else None,
                "expected": json.loads(r[4]) if r[4] else None,
                "answer": r[5],
                "grade": json.loads(r[6]) if r[6] else None,
                "decision": r[7],
                "mastery_after": r[8],
                "created_at": r[9],
            }
            for r in rows
        ]

    def save_mastery(
        self,
        session_id: str,
        learner_id: str,
        entry_id: str,
        round_no: int,
        correctness: bool,
        mastery_after: float,
    ) -> None:
        db = self._connect()
        try:
            db.execute(
                """INSERT INTO mastery_snapshots
                   (session_id, learner_id, entry_id, round_no, correctness, mastery_after, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    learner_id,
                    entry_id,
                    round_no,
                    int(correctness),
                    mastery_after,
                    _now(),
                ),
            )
            db.commit()
        finally:
            db.close()

    def load_mastery_history(self, session_id: str, entry_id: str) -> list[bool]:
        """某条目的作答对错序列（时间正序）——掌握度计算输入。"""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT correctness FROM mastery_snapshots "
                "WHERE session_id=? AND entry_id=? ORDER BY id",
                (session_id, entry_id),
            ).fetchall()
        finally:
            db.close()
        return [bool(r[0]) for r in rows]

    def load_mastery_report(self, session_id: str) -> list[dict[str, Any]]:
        """全条目掌握度快照（报告页雷达/曲线数据源）。"""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT entry_id, round_no, correctness, mastery_after "
                "FROM mastery_snapshots WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "entry_id": r[0],
                "round_no": r[1],
                "correctness": bool(r[2]),
                "mastery_after": r[3],
            }
            for r in rows
        ]

    # ---------- 资源包 ----------

    def upsert_package(
        self,
        session_id: str,
        learner_id: str,
        entry_id: str,
        *,
        lecture: list[dict[str, Any]] | None = None,
        questions: list[dict[str, Any]] | None = None,
        practice: dict[str, Any] | None = None,
        challenge: dict[str, Any] | None = None,
        difficulty_tier: str = "",
    ) -> None:
        """重教后追加资源：先读旧包合并，再 upsert（UNIQUE(session_id, entry_id)）。"""
        db = self._connect()
        try:
            row = db.execute(
                "SELECT lecture_json, questions_json FROM resource_packages "
                "WHERE session_id=? AND entry_id=?",
                (session_id, entry_id),
            ).fetchone()
            old_lecture = json.loads(row[0]) if row and row[0] else []
            old_questions = json.loads(row[1]) if row and row[1] else []

            merged_lecture = old_lecture + (lecture or [])
            # 题目按 question_id 去重（重教后缓存失效重生成，保留最新版）
            by_id: dict[str, dict[str, Any]] = {q["question_id"]: q for q in old_questions}
            for q in questions or []:
                by_id[q["question_id"]] = q

            db.execute(
                """INSERT INTO resource_packages
                   (session_id, learner_id, entry_id, lecture_json, questions_json,
                    practice_json, challenge_json, difficulty_tier, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id, entry_id) DO UPDATE SET
                     lecture_json=excluded.lecture_json,
                     questions_json=excluded.questions_json,
                     practice_json=COALESCE(excluded.practice_json, resource_packages.practice_json),
                     challenge_json=COALESCE(excluded.challenge_json, resource_packages.challenge_json),
                     difficulty_tier=excluded.difficulty_tier""",
                (
                    session_id,
                    learner_id,
                    entry_id,
                    json.dumps(merged_lecture, ensure_ascii=False),
                    json.dumps(list(by_id.values()), ensure_ascii=False),
                    json.dumps(practice, ensure_ascii=False) if practice else None,
                    json.dumps(challenge, ensure_ascii=False) if challenge else None,
                    difficulty_tier,
                    _now(),
                ),
            )
            db.commit()
        finally:
            db.close()

    def load_packages(self, session_id: str) -> list[dict[str, Any]]:
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT session_id, learner_id, entry_id, lecture_json, questions_json, "
                "practice_json, challenge_json, difficulty_tier, created_at "
                "FROM resource_packages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "session_id": r[0],
                "learner_id": r[1],
                "entry_id": r[2],
                "lecture": json.loads(r[3]) if r[3] else [],
                "questions": json.loads(r[4]) if r[4] else [],
                "practice": json.loads(r[5]) if r[5] else None,
                "challenge": json.loads(r[6]) if r[6] else None,
                "difficulty_tier": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]

    # ---------- 条目化导出产物 ----------

    def save_export_entries(
        self, session_id: str, entries: list[dict[str, Any]]
    ) -> None:
        """落库条目化导出产物（知识库同构条目，FR-23）。

        entries 每条 = SeedEntry 同构字段 + source_entry_id；
        重导出覆盖同 id 条目（UNIQUE(session_id, entry_id)）。
        """
        db = self._connect()
        try:
            for e in entries:
                entry_json = {k: v for k, v in e.items() if k != "source_entry_id"}
                db.execute(
                    """INSERT INTO exported_entries
                       (session_id, entry_id, source_entry_id, entry_json, exported_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(session_id, entry_id) DO UPDATE SET
                         source_entry_id=excluded.source_entry_id,
                         entry_json=excluded.entry_json,
                         exported_at=excluded.exported_at""",
                    (
                        session_id,
                        e["id"],
                        e.get("source_entry_id", ""),
                        json.dumps(entry_json, ensure_ascii=False),
                        _now(),
                    ),
                )
            db.commit()
        finally:
            db.close()

    def load_export_entries(self, session_id: str) -> list[dict[str, Any]]:
        """读条目化导出产物：SeedEntry 同构字段 + source_entry_id + exported_at。"""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT source_entry_id, entry_json, exported_at "
                "FROM exported_entries WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        finally:
            db.close()
        return [
            {"source_entry_id": r[0], "exported_at": r[2], **json.loads(r[1])}
            for r in rows
        ]

    # ---------- 会话清理与跨会话聚合 ----------

    def delete_session(
        self,
        session_id: str,
        *,
        keep_packages: bool = False,
        keep_exports: bool = False,
    ) -> dict[str, int]:
        """删除会话与过程数据，返回各表删除行数。

        事件/轮次/掌握度快照是教学过程数据，随会话删除；资源包与条目化导出是
        沉淀产物，可按参数保留（成为无会话归属的孤儿行，仍可在资源库聚合展示，
        删除会话前其 learner_id/session_id 已落在行内，溯源信息不丢）。
        """
        deleted: dict[str, int] = {}
        db = self._connect()
        try:
            cur = db.execute("DELETE FROM session_events WHERE session_id=?", (session_id,))
            deleted["events"] = cur.rowcount
            cur = db.execute("DELETE FROM topic_rounds WHERE session_id=?", (session_id,))
            deleted["rounds"] = cur.rowcount
            cur = db.execute("DELETE FROM mastery_snapshots WHERE session_id=?", (session_id,))
            deleted["snapshots"] = cur.rowcount
            if not keep_packages:
                cur = db.execute("DELETE FROM resource_packages WHERE session_id=?", (session_id,))
                deleted["packages"] = cur.rowcount
            if not keep_exports:
                cur = db.execute("DELETE FROM exported_entries WHERE session_id=?", (session_id,))
                deleted["exports"] = cur.rowcount
            db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            db.commit()
        finally:
            db.close()
        return deleted

    def load_all_packages(
        self, *, session_id: str | None = None, entry_id: str | None = None
    ) -> list[dict[str, Any]]:
        """跨会话聚合资源包（资源库页面数据源），可按来源会话/条目筛选。

        LEFT JOIN sessions：会话被删后保留的包 session_status 为 None（孤儿行）。
        """
        db = self._connect()
        try:
            rows = db.execute(
                """SELECT rp.session_id, rp.learner_id, rp.entry_id, rp.lecture_json,
                          rp.questions_json, rp.practice_json, rp.challenge_json,
                          rp.difficulty_tier, rp.created_at, s.status
                   FROM resource_packages rp
                   LEFT JOIN sessions s ON s.session_id = rp.session_id
                   WHERE (? IS NULL OR rp.session_id = ?)
                     AND (? IS NULL OR rp.entry_id = ?)
                   ORDER BY rp.id""",
                (session_id, session_id, entry_id, entry_id),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "session_id": r[0],
                "learner_id": r[1],
                "entry_id": r[2],
                "lecture": json.loads(r[3]) if r[3] else [],
                "questions": json.loads(r[4]) if r[4] else [],
                "practice": json.loads(r[5]) if r[5] else None,
                "challenge": json.loads(r[6]) if r[6] else None,
                "difficulty_tier": r[7],
                "created_at": r[8],
                "session_status": r[9],
            }
            for r in rows
        ]

    def load_all_export_entries(
        self, *, session_id: str | None = None, entry_id: str | None = None
    ) -> list[dict[str, Any]]:
        """跨会话聚合条目化导出产物（资源库页面数据源），可按来源会话/源条目筛选。

        entry_id 筛选命中源条目或生成条目（两种引用方式都常见）。
        """
        db = self._connect()
        try:
            rows = db.execute(
                """SELECT ee.session_id, ee.source_entry_id, ee.entry_json, ee.exported_at,
                          s.learner_id, s.status
                   FROM exported_entries ee
                   LEFT JOIN sessions s ON s.session_id = ee.session_id
                   WHERE (? IS NULL OR ee.session_id = ?)
                     AND (? IS NULL OR ee.source_entry_id = ? OR json_extract(ee.entry_json, '$.id') = ?)
                   ORDER BY ee.id""",
                (session_id, session_id, entry_id, entry_id, entry_id),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "session_id": r[0],
                "source_entry_id": r[1],
                "exported_at": r[3],
                "learner_id": r[4],
                "session_status": r[5],
                **json.loads(r[2]),
            }
            for r in rows
        ]


__all__ = ["SessionEvent", "SessionStore"]
