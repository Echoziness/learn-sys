"""init_db 幂等性：反复运行条目数/向量数不变，不崩 UNIQUE 约束（重构前的已验证 bug）。"""

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
    db.close()
    assert updates == 1


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
        "SELECT count(*) FROM knowledge_vec v LEFT JOIN knowledge_entries k ON k.rowid = v.rowid WHERE k.rowid IS NULL"
    ).fetchone()[0]
    content = db.execute("SELECT content FROM knowledge_entries WHERE id='T-001'").fetchone()[0]
    db.close()
    assert orphans == 0
    assert content == "内容一（修订版）"
