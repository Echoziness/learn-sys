"""SessionStore：事件流 seq 单调、订阅推送、进度推导数据源、资源包合并。"""

import asyncio
import sqlite3
from pathlib import Path

import sqlite_vec
from scripts.init_db import SCHEMA

from core.session import SessionEvent, SessionStore


def make_store(tmp_path: Path) -> SessionStore:
    db = sqlite3.connect(tmp_path / "test.db")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.executescript(SCHEMA.replace("{vec_dim}", "8"))
    db.close()
    return SessionStore(str(tmp_path / "test.db"))


def test_session_lifecycle(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session("test1", {"background": {"education": "本科"}})
    store.save_diagnosis(
        sid, gap_ids=["E1"], difficulty_level="beginner",
        profile_summary="摘要", plan={"topics": [{"entry_id": "E1"}]},
    )
    session = store.get_session(sid)
    assert session is not None
    assert session["learner_id"] == "test1"
    assert session["gap_ids"] == ["E1"]
    assert session["plan"]["topics"][0]["entry_id"] == "E1"
    store.finish_session(sid)
    finished = store.get_session(sid)
    assert finished is not None and finished["status"] == "finished"


def test_emit_seq_monotonic_and_load(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session("test1", {})
    for i in range(5):
        asyncio.run(store.emit(sid, "plan_done", {"i": i}))
    events = store.load_events(sid)
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert [e.payload["i"] for e in events] == [0, 1, 2, 3, 4]
    # after_seq 补发
    assert [e.seq for e in store.load_events(sid, after_seq=3)] == [4, 5]


def test_subscribe_receives_events(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session("test1", {})

    async def run():
        queue = store.subscribe(sid)
        await store.emit(sid, "topic_start", {"entry_id": "E1"})
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event.event_type == "topic_start"
        assert event.payload == {"entry_id": "E1"}
        store.unsubscribe(sid, queue)
        # 退订后不再接收（不抛错即可）
        await store.emit(sid, "topic_start", {"entry_id": "E2"})
        assert queue.empty()

    asyncio.run(run())


def test_round_and_mastery_history(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session("test1", {})
    store.save_round(
        sid, entry_id="E1", round_no=1,
        question={"prompt": "题干", "question_type": "answer"},
        expected=["主键", "外键"], answer="主键",
        grade={"verdict": "partial"}, decision="retry", mastery_after=0.5,
    )
    store.save_mastery(sid, "test1", "E1", 1, correctness=False, mastery_after=0.0)
    store.save_mastery(sid, "test1", "E1", 2, correctness=True, mastery_after=0.5)

    rounds = store.load_rounds(sid, "E1")
    assert len(rounds) == 1
    assert rounds[0]["expected"] == ["主键", "外键"]
    assert rounds[0]["decision"] == "retry"
    # 进度推导数据源：掌握度历史即作答对错序列
    assert store.load_mastery_history(sid, "E1") == [False, True]


def test_package_merge_and_dedupe(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session("test1", {})
    store.upsert_package(
        sid, "test1", "E1",
        lecture=[{"text": "论断1", "evidence_ids": ["E1"]}],
        questions=[{"question_id": "q1", "prompt": "旧题"}],
        difficulty_tier="beginner",
    )
    # 重教后：追加讲义、q1 重生成（同 id 覆盖）、新增 q2、practice 不回退
    store.upsert_package(
        sid, "test1", "E1",
        lecture=[{"text": "论断2", "evidence_ids": ["E1"]}],
        questions=[
            {"question_id": "q1", "prompt": "新题"},
            {"question_id": "q2", "prompt": "追加题"},
        ],
        difficulty_tier="beginner",
    )
    pkgs = store.load_packages(sid)
    assert len(pkgs) == 1
    p = pkgs[0]
    assert [c["text"] for c in p["lecture"]] == ["论断1", "论断2"]
    prompts = {q["question_id"]: q["prompt"] for q in p["questions"]}
    assert prompts == {"q1": "新题", "q2": "追加题"}
    assert p["difficulty_tier"] == "beginner"


def test_package_practice_kept_on_merge(tmp_path):
    """重教轮 upsert 不带 practice 时，旧指南保留（COALESCE）。"""
    store = make_store(tmp_path)
    sid = store.create_session("test1", {})
    store.upsert_package(
        sid, "test1", "E1",
        practice={"steps": ["步骤1"]}, difficulty_tier="beginner",
    )
    store.upsert_package(sid, "test1", "E1", lecture=[{"text": "论断"}], difficulty_tier="beginner")
    p = store.load_packages(sid)[0]
    assert p["practice"] == {"steps": ["步骤1"]}
    assert len(p["lecture"]) == 1


def test_event_type_is_dataclass(tmp_path):
    """SessionEvent 为 frozen dataclass——事件不可变，符合审计语义。"""
    e = SessionEvent("s", 1, "topic_start", {}, "2026")
    try:
        e.seq = 2  # type: ignore[misc]
        raise AssertionError("应不可变")
    except AttributeError:
        pass
