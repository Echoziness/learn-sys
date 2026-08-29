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


def _seed_entry(gen_id: str, source_id: str) -> dict[str, str]:
    """最小导出条目（仅测存储层，不追求 SeedEntry 完整字段）。"""
    return {"id": gen_id, "source_entry_id": source_id, "title": "t", "content": "c"}


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


def test_list_sessions(tmp_path):
    store = make_store(tmp_path)
    s1 = store.create_session("alpha", {})
    store.save_diagnosis(
        s1, gap_ids=[], difficulty_level="beginner", profile_summary="", plan={},
    )
    asyncio.run(store.emit(s1, "session_start", {}))
    s2 = store.create_session("beta", {})
    store.save_diagnosis(
        s2, gap_ids=[], difficulty_level="advanced", profile_summary="",
        plan={"topics": [{"entry_id": "A"}, {"entry_id": "B"}]},
    )
    items = store.list_sessions()
    # 创建时间倒序：后创建的在前
    assert [i["learner_id"] for i in items] == ["beta", "alpha"]
    assert items[0]["difficulty_level"] == "advanced"
    assert items[0]["topic_count"] == 2
    assert items[1]["event_count"] == 1
    store.finish_session(s2)
    assert store.list_sessions()[0]["status"] == "finished"


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


def test_try_claim_round_blocks_concurrent_double_submit(tmp_path):
    """原子占用：并发双提交只有第一个成功（2026-08-29 实测 409 故障回归）。"""
    store = make_store(tmp_path)
    sid = store.create_session("u1", {})
    store.save_round(
        sid, entry_id="E1", round_no=1,
        question={"question_id": "q_E1_r1_answer", "question_type": "answer", "prompt": "？"},
        expected=["甲"], answer=None, grade=None, decision="pending", mastery_after=None,
    )
    assert store.try_claim_round(sid, "E1", 1) is True
    # 第二个请求穿入：占用失败（终判落地前可重试作答，落地后同样失败）
    assert store.try_claim_round(sid, "E1", 1) is False
    store.update_round_answer(
        sid, "E1", 1, answer="甲", grade={"is_correct": True},
        decision="advance", mastery_after=0.5,
    )
    assert store.try_claim_round(sid, "E1", 1) is False
    # 不存在的轮：占用失败（无待答题目）
    assert store.try_claim_round(sid, "E1", 99) is False


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


def test_export_entries_roundtrip_and_overwrite(tmp_path):
    """条目化导出产物落库：同构字段往返 + 重导出覆盖同 id 条目。"""
    store = make_store(tmp_path)
    sid = store.create_session("p01", {})
    entry = {
        "id": "GEN-E1-p01",
        "source_entry_id": "E1",
        "knowledge_type": "concept",
        "title": "测试条目（进阶适配版）",
        "content": "内容正文。",
        "prerequisites": ["E0"],
        "difficulty": 2,
        "keywords": ["内容"],
        "source": "生成自 E1；审核通过 2/3 论断",
    }
    store.save_export_entries(sid, [entry])
    loaded = store.load_export_entries(sid)
    assert len(loaded) == 1
    assert loaded[0] == {**entry, "exported_at": loaded[0]["exported_at"]}
    assert "source_entry_id" in loaded[0] and loaded[0]["exported_at"]

    # 重导出同 id：覆盖而非追加，且 source_entry_id 不可丢进 entry_json 同构体之外破坏字段集
    store.save_export_entries(sid, [{**entry, "content": "重写后的正文。", "source_entry_id": "E2"}])
    loaded = store.load_export_entries(sid)
    assert len(loaded) == 1
    assert loaded[0]["content"] == "重写后的正文。"
    assert loaded[0]["source_entry_id"] == "E2"


def test_delete_session_keep_flags(tmp_path):
    """删除会话：过程数据必删；资源包/导出条目可选保留为孤儿行（溯源字段仍在行内）。"""
    store = make_store(tmp_path)
    sid = store.create_session("test1", {})
    asyncio.run(store.emit(sid, "session_start", {}))
    store.save_round(
        sid, entry_id="E1", round_no=1,
        question={"prompt": "题干"}, expected=["主键"], answer="主键",
        grade={"verdict": "correct"}, decision="advance", mastery_after=0.8,
    )
    store.save_mastery(sid, "test1", "E1", 1, correctness=True, mastery_after=0.8)
    store.upsert_package(sid, "test1", "E1", lecture=[{"text": "论断"}], difficulty_tier="beginner")
    store.save_export_entries(sid, [_seed_entry("GEN-E1-test1", "E1")])

    deleted = store.delete_session(sid, keep_packages=True, keep_exports=True)
    assert deleted == {"events": 1, "rounds": 1, "snapshots": 1}
    assert store.get_session(sid) is None
    assert store.load_events(sid) == []
    # 保留的产物成孤儿行：session_status/learner_id 为 None，条目本体完整
    pkgs = store.load_all_packages()
    assert len(pkgs) == 1
    assert pkgs[0]["session_status"] is None and pkgs[0]["learner_id"] == "test1"
    exports = store.load_all_export_entries()
    assert len(exports) == 1
    assert exports[0]["session_status"] is None and exports[0]["id"] == "GEN-E1-test1"

    # 全删模式：产物一并清除，各表行数如实回报
    sid2 = store.create_session("test2", {})
    store.upsert_package(sid2, "test2", "E2", lecture=[{"text": "论断"}], difficulty_tier="beginner")
    store.save_export_entries(sid2, [_seed_entry("GEN-E2-test2", "E2")])
    deleted2 = store.delete_session(sid2)
    assert deleted2["packages"] == 1 and deleted2["exports"] == 1
    assert [p["entry_id"] for p in store.load_all_packages()] == ["E1"]
    assert [e["id"] for e in store.load_all_export_entries()] == ["GEN-E1-test1"]


def test_load_all_packages_and_exports_with_filters(tmp_path):
    """跨会话聚合：无参全量，可按来源会话/条目筛选（导出侧源条目与生成条目都命中）。"""
    store = make_store(tmp_path)
    sa = store.create_session("alpha", {})
    sb = store.create_session("beta", {})
    store.upsert_package(sa, "alpha", "E1", lecture=[{"text": "a"}], difficulty_tier="beginner")
    store.upsert_package(sb, "beta", "E2", lecture=[{"text": "b"}], difficulty_tier="advanced")
    store.save_export_entries(sa, [_seed_entry("GEN-E1-alpha", "E1")])
    store.save_export_entries(sb, [_seed_entry("GEN-E2-beta", "E2")])

    assert len(store.load_all_packages()) == 2
    by_session = store.load_all_packages(session_id=sa)
    assert [p["entry_id"] for p in by_session] == ["E1"]
    assert by_session[0]["session_status"] == "active"
    assert [p["entry_id"] for p in store.load_all_packages(entry_id="E2")] == ["E2"]

    assert len(store.load_all_export_entries()) == 2
    assert [e["id"] for e in store.load_all_export_entries(session_id=sb)] == ["GEN-E2-beta"]
    # 筛选既命中源条目也命中生成条目（两种引用方式都常见）
    assert [e["id"] for e in store.load_all_export_entries(entry_id="E1")] == ["GEN-E1-alpha"]
    assert [e["id"] for e in store.load_all_export_entries(entry_id="GEN-E2-beta")] == ["GEN-E2-beta"]
    assert store.load_all_export_entries(entry_id="不存在") == []
    # 未删会话的导出条目带来源会话信息（删除后转 None，见 keep_flags 用例）
    assert store.load_all_export_entries(session_id=sa)[0]["learner_id"] == "alpha"


def test_event_type_is_dataclass(tmp_path):
    """SessionEvent 为 frozen dataclass——事件不可变，符合审计语义。"""
    e = SessionEvent("s", 1, "topic_start", {}, "2026")
    try:
        e.seq = 2  # type: ignore[misc]
        raise AssertionError("应不可变")
    except AttributeError:
        pass
