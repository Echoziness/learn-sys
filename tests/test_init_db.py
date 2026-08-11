"""init_db 幂等性：反复运行条目数/向量数不变，不崩 UNIQUE 约束（重构前的已验证 bug）。
旧库（无 knowledge_type 列）重跑时幂等补列（ALTER TABLE，保留运行时数据与 rowid 对齐）。"""

import json
import sqlite3

import pytest
import sqlite_vec
from scripts.init_db import init_database


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture
def seed_dir(tmp_path):
    domain_dir = tmp_path / "seeds" / "test-domain"
    domain_dir.mkdir(parents=True)
    (domain_dir / "entries.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "T-001",
                        "knowledge_type": "memory",
                        "title": "条目一",
                        "content": "内容一",
                        "prerequisites": [],
                        "difficulty": 1,
                        "keywords": ["测试"],
                        "source": "test",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "id": "T-002",
                        "title": "条目二",
                        "content": "内容二",
                        "prerequisites": ["T-001"],
                        "difficulty": 2,
                        "keywords": ["测试"],
                        "source": "test",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    profiles_dir = tmp_path / "seeds" / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "u1.json").write_text(
        json.dumps(
            {
                "learner_id": "u1",
                "name": "测试学员",
                "background": {"goal": "测试"},
                "mastery": {},
                "style_tags": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path / "seeds"


def test_init_twice_is_idempotent(seed_dir, tmp_path):
    db_path = str(tmp_path / "knowledge.db")
    first = init_database(db_path, seed_dir, _fake_embed)
    second = init_database(db_path, seed_dir, _fake_embed)

    assert first == second == {"domains": 1, "entries": 2, "vec": 2, "fts": 2, "profiles": 1}

    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    # 画像未变化时不重复记录 profile_updates
    updates = db.execute("SELECT count(*) FROM profile_updates WHERE learner_id='u1'").fetchone()[0]
    # knowledge_type 落库：显式值与默认值（concept）
    types = {
        eid: kt
        for eid, kt in db.execute("SELECT id, knowledge_type FROM knowledge_entries")
    }
    db.close()
    assert updates == 1
    assert types == {"T-001": "memory", "T-002": "concept"}


def test_old_schema_db_gets_knowledge_type_column(seed_dir, tmp_path):
    """升级前旧库（无 knowledge_type 列）重跑 init_db：幂等补列、保留既有行、可再跑。

    选择 ALTER TABLE 迁移而非删库重建：运行时数据（画像/会话记录）与 rowid
    对齐均不受影响；补列后继续按 upsert 逻辑写入种子。
    """
    db_path = str(tmp_path / "knowledge.db")
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE knowledge_entries (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL DEFAULT 'bigdata-analysis',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            prerequisites TEXT,
            difficulty INTEGER CHECK(difficulty BETWEEN 1 AND 5),
            keywords TEXT,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    db.execute(
        "INSERT INTO knowledge_entries"
        "(id, domain, title, content, prerequisites, difficulty, keywords, source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("OLD-001", "test-domain", "旧条目", "旧内容", "[]", 1, "[]", "old"),
    )
    db.commit()
    db.close()

    counts = init_database(db_path, seed_dir, _fake_embed)
    assert counts["entries"] == 3

    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)")}
    assert "knowledge_type" in cols
    # 旧行未丢、补列后取默认值 concept
    old_type = db.execute(
        "SELECT knowledge_type FROM knowledge_entries WHERE id='OLD-001'"
    ).fetchone()[0]
    db.close()
    assert old_type == "concept"

    # 迁移完成后重跑依旧幂等
    again = init_database(db_path, seed_dir, _fake_embed)
    assert again == counts


def test_entry_update_preserves_rowid_alignment(seed_dir, tmp_path):
    """条目内容更新后，vec 表仍与 entries 表行对齐（不重复、不错位）。"""
    db_path = str(tmp_path / "knowledge.db")
    init_database(db_path, seed_dir, _fake_embed)

    entries_file = seed_dir / "test-domain" / "entries.jsonl"
    lines = entries_file.read_text(encoding="utf-8").splitlines()
    updated = json.loads(lines[0])
    updated["content"] = "内容一（修订版）"
    lines[0] = json.dumps(updated, ensure_ascii=False)
    entries_file.write_text("\n".join(lines), encoding="utf-8")

    counts = init_database(db_path, seed_dir, _fake_embed)
    assert counts["vec"] == 2

    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    orphans = db.execute(
        "SELECT count(*) FROM knowledge_vec v LEFT JOIN knowledge_entries k "
        "ON k.rowid = v.rowid WHERE k.rowid IS NULL"
    ).fetchone()[0]
    content = db.execute("SELECT content FROM knowledge_entries WHERE id='T-001'").fetchone()[0]
    db.close()
    assert orphans == 0
    assert content == "内容一（修订版）"
