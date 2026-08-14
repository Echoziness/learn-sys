"""会话层 5 张新表的 schema 与幂等迁移验证（架构文档 §3.2）。"""

import sqlite3

import sqlite_vec
from scripts.init_db import SCHEMA

SESSION_TABLES = (
    "sessions",
    "session_events",
    "topic_rounds",
    "mastery_snapshots",
    "resource_packages",
)


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.executescript(SCHEMA.replace("{vec_dim}", "8"))
    return db


def test_session_tables_created():
    db = _make_db()
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in SESSION_TABLES:
        assert t in tables, f"缺表 {t}"
    db.close()


def test_events_seq_unique_constraint():
    """seq 在会话内必须唯一——回放排序键的完整性保证。"""
    db = _make_db()
    db.execute(
        "INSERT INTO session_events(session_id, seq, event_type, payload_json, created_at) "
        "VALUES ('s1', 1, 'session_start', '{}', '2026-08-14')"
    )
    try:
        db.execute(
            "INSERT INTO session_events(session_id, seq, event_type, payload_json, created_at) "
            "VALUES ('s1', 1, 'plan_done', '{}', '2026-08-14')"
        )
        raise AssertionError("重复 seq 应被 UNIQUE 约束拒绝")
    except sqlite3.IntegrityError:
        pass
    # 不同会话的 seq 互不影响
    db.execute(
        "INSERT INTO session_events(session_id, seq, event_type, payload_json, created_at) "
        "VALUES ('s2', 1, 'session_start', '{}', '2026-08-14')"
    )
    db.close()


def test_resource_package_unique_per_entry():
    """同一会话同一条目只允许一个资源包（重教时 upsert 合并）。"""
    db = _make_db()
    db.execute(
        "INSERT INTO resource_packages(session_id, learner_id, entry_id, difficulty_tier, created_at) "
        "VALUES ('s1', 'test1', 'E1', 'beginner', '2026-08-14')"
    )
    try:
        db.execute(
            "INSERT INTO resource_packages(session_id, learner_id, entry_id, difficulty_tier, created_at) "
            "VALUES ('s1', 'test1', 'E1', 'beginner', '2026-08-14')"
        )
        raise AssertionError("重复资源包应被 UNIQUE 约束拒绝")
    except sqlite3.IntegrityError:
        pass
    db.close()


def test_legacy_db_idempotent_migration():
    """旧库（无会话表）跑 SCHEMA 后补齐 5 表且数据无损。"""
    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    # 模拟旧库：只有 knowledge_entries
    db.execute(
        "CREATE TABLE knowledge_entries (id TEXT PRIMARY KEY, title TEXT NOT NULL)"
    )
    db.execute("INSERT INTO knowledge_entries VALUES ('E1', '旧数据')")
    db.executescript(SCHEMA.replace("{vec_dim}", "8"))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in SESSION_TABLES:
        assert t in tables
    # 旧数据无损
    assert db.execute("SELECT title FROM knowledge_entries WHERE id='E1'").fetchone()[0] == "旧数据"
    # 反复执行幂等
    db.executescript(SCHEMA.replace("{vec_dim}", "8"))
    assert db.execute("SELECT count(*) FROM knowledge_entries").fetchone()[0] == 1
    db.close()
