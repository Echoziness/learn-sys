#!/usr/bin/env python3
"""初始化知识库：从 data/seeds/ 加载条目与画像 → 建表 → upsert → 重建 FTS → 重算 embedding。

幂等设计，可反复运行：条目按 id upsert（保留 rowid），向量按 rowid 删除重写，FTS 全量重建。
数据与代码分离——条目维护只需编辑 data/seeds/<domain>/entries.jsonl，无需触碰本脚本。
"""

import json
import sqlite3
import struct
from collections.abc import Callable
from pathlib import Path

import sqlite_vec
import structlog
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from core.retrieval import fts_document

logger = structlog.get_logger()

SCHEMA = """
    CREATE TABLE IF NOT EXISTS knowledge_entries (
        id          TEXT PRIMARY KEY,
        domain      TEXT NOT NULL DEFAULT 'bigdata-analysis',
        title       TEXT NOT NULL,
        content     TEXT NOT NULL,
        prerequisites TEXT,          -- JSON array of entry IDs
        difficulty  INTEGER CHECK(difficulty BETWEEN 1 AND 5),
        keywords    TEXT,            -- JSON array
        source      TEXT,
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );

    -- 全文检索（独立表，loader 直写；entry_id 直连条目，文本经 CJK 逐字切分）
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
        entry_id UNINDEXED,
        text
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec USING vec0(
        embedding FLOAT[{vec_dim}]
    );

    CREATE TABLE IF NOT EXISTS learners (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS learner_profiles (
        learner_id  TEXT PRIMARY KEY REFERENCES learners(id),
        background  TEXT NOT NULL,   -- JSON
        mastery     TEXT,            -- JSON: {entry_id: 0.0-1.0}
        style_tags  TEXT,            -- JSON
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS profile_updates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        learner_id  TEXT NOT NULL REFERENCES learners(id),
        source      TEXT NOT NULL,   -- initial_test / assessment / feedback
        detail      TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS run_history (
        id            TEXT PRIMARY KEY,
        learner_id    TEXT NOT NULL REFERENCES learners(id),
        status        TEXT DEFAULT 'pending',
        start_at      TEXT,
        end_at        TEXT,
        state_snapshot TEXT,
        created_at    TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS generated_resources (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL REFERENCES run_history(id),
        learner_id    TEXT NOT NULL REFERENCES learners(id),
        resource_type TEXT NOT NULL, -- lecture / guide / quiz
        content       TEXT NOT NULL, -- JSON
        evidence_ids  TEXT,          -- JSON
        review_verdict TEXT,
        created_at    TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS conversation_logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        learner_id  TEXT NOT NULL REFERENCES learners(id),
        source      TEXT NOT NULL,   -- user / agent-diagnose / agent-generate / agent-review
        content     TEXT NOT NULL,
        run_id      TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS assessment_results (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        learner_id  TEXT NOT NULL REFERENCES learners(id),
        run_id      TEXT REFERENCES run_history(id),
        answers     TEXT NOT NULL,   -- JSON
        score       REAL,
        created_at  TEXT DEFAULT (datetime('now'))
    );
"""


class SeedEntry(BaseModel):
    id: str
    title: str
    content: str
    prerequisites: list[str] = Field(default_factory=list)
    difficulty: int = Field(ge=1, le=5)
    keywords: list[str] = Field(default_factory=list)
    source: str = ""


class SeedProfile(BaseModel):
    learner_id: str
    name: str
    background: dict
    mastery: dict[str, float] = Field(default_factory=dict)
    style_tags: list[str] = Field(default_factory=list)
    note: str = ""


def load_entries(seed_dir: Path) -> dict[str, list[SeedEntry]]:
    """扫描 seed_dir 下每个领域目录的 entries.jsonl。目录名即 domain——换库即换领域。"""
    domains: dict[str, list[SeedEntry]] = {}
    for entries_file in sorted(seed_dir.glob("*/entries.jsonl")):
        domain = entries_file.parent.name
        with open(entries_file, encoding="utf-8") as f:
            domains[domain] = [SeedEntry.model_validate_json(line) for line in f if line.strip()]
    return domains


def load_profiles(seed_dir: Path) -> list[SeedProfile]:
    profiles_dir = seed_dir / "profiles"
    if not profiles_dir.exists():
        return []
    return [
        SeedProfile.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(profiles_dir.glob("*.json"))
    ]


def _connect(db_path: str) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    return db


def ensure_schema(db: sqlite3.Connection, vec_dim: int) -> None:
    # fts/vec 是派生数据，每次重建：tokenizer 或维度变化时自愈，且保证幂等
    db.execute("DROP TABLE IF EXISTS knowledge_fts")
    db.execute("DROP TABLE IF EXISTS knowledge_vec")
    # SCHEMA 注释含 JSON 示例花括号，只能用 replace 不能用 str.format
    db.executescript(SCHEMA.replace("{vec_dim}", str(vec_dim)))


def sync_fts(db: sqlite3.Connection, entries: list[SeedEntry]) -> None:
    for e in entries:
        db.execute(
            "INSERT INTO knowledge_fts(entry_id, text) VALUES (?, ?)",
            (e.id, fts_document(e.title, e.content, e.keywords)),
        )


def upsert_entries(db: sqlite3.Connection, domain: str, entries: list[SeedEntry]) -> None:
    """按 id upsert。ON CONFLICT DO UPDATE 保留 rowid，维持 FTS/vec 外部内容表的行对齐。"""
    for e in entries:
        db.execute(
            """INSERT INTO knowledge_entries
               (id, domain, title, content, prerequisites, difficulty, keywords, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 domain=excluded.domain, title=excluded.title, content=excluded.content,
                 prerequisites=excluded.prerequisites, difficulty=excluded.difficulty,
                 keywords=excluded.keywords, source=excluded.source,
                 updated_at=datetime('now')""",
            (
                e.id, domain, e.title, e.content,
                json.dumps(e.prerequisites, ensure_ascii=False),
                e.difficulty,
                json.dumps(e.keywords, ensure_ascii=False),
                e.source,
            ),
        )


def sync_vectors(
    db: sqlite3.Connection,
    entries: list[SeedEntry],
    embeddings: list[list[float]],
) -> int:
    """按 rowid 删除重写向量，保证反复运行不产生重复或错位。"""
    synced = 0
    for entry, emb in zip(entries, embeddings, strict=True):
        row = db.execute("SELECT rowid FROM knowledge_entries WHERE id=?", (entry.id,)).fetchone()
        if row is None:
            raise RuntimeError(f"条目 {entry.id} 未入库，无法同步向量")
        dim = len(emb)
        db.execute("DELETE FROM knowledge_vec WHERE rowid=?", (row[0],))
        db.execute(
            "INSERT INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
            (row[0], struct.pack(f"{dim}f", *emb)),
        )
        synced += 1
    return synced


def upsert_profile(db: sqlite3.Connection, profile: SeedProfile) -> None:
    db.execute(
        "INSERT INTO learners(id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
        (profile.learner_id, profile.name),
    )
    new_row = (
        json.dumps(profile.background, ensure_ascii=False),
        json.dumps(profile.mastery, ensure_ascii=False),
        json.dumps(profile.style_tags, ensure_ascii=False),
    )
    old = db.execute(
        "SELECT background, mastery, style_tags FROM learner_profiles WHERE learner_id=?",
        (profile.learner_id,),
    ).fetchone()
    db.execute(
        """INSERT INTO learner_profiles(learner_id, background, mastery, style_tags)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(learner_id) DO UPDATE SET
             background=excluded.background, mastery=excluded.mastery,
             style_tags=excluded.style_tags, updated_at=datetime('now')""",
        (profile.learner_id, *new_row),
    )
    if old is None or tuple(old) != new_row:
        db.execute(
            "INSERT INTO profile_updates(learner_id, source, detail) VALUES (?, 'initial_test', ?)",
            (profile.learner_id, json.dumps({"method": "seed", "note": profile.note}, ensure_ascii=False)),
        )


def init_database(
    db_path: str,
    seed_dir: Path,
    embed_batch: Callable[[list[str]], list[list[float]]],
) -> dict[str, int]:
    """完整初始化流程。embed_batch 注入使本函数可用 fake encoder 测试。"""
    domains = load_entries(seed_dir)
    profiles = load_profiles(seed_dir)
    all_entries = [e for entries in domains.values() for e in entries]
    if not all_entries:
        raise RuntimeError(f"{seed_dir} 下未找到任何 entries.jsonl")

    embeddings = embed_batch([e.content for e in all_entries])
    vec_dim = len(embeddings[0])

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = _connect(db_path)
    try:
        ensure_schema(db, vec_dim)
        for domain, entries in domains.items():
            upsert_entries(db, domain, entries)
        sync_fts(db, all_entries)
        sync_vectors(db, all_entries, embeddings)
        for profile in profiles:
            upsert_profile(db, profile)
        db.commit()

        return {
            "domains": len(domains),
            "entries": db.execute("SELECT count(*) FROM knowledge_entries").fetchone()[0],
            "vec": db.execute("SELECT count(*) FROM knowledge_vec").fetchone()[0],
            "fts": db.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0],
            "profiles": len(profiles),
        }
    finally:
        db.close()


def main() -> None:
    from core.config import Settings
    from core.embedding import BGEEncoder
    from core.logging import configure_logging

    load_dotenv()
    configure_logging()

    settings = Settings.from_env(require_llm=False)
    logger.info("init_db_start", db=settings.database_path, seed_dir=settings.seed_dir)

    encoder = BGEEncoder(cache_folder=settings.bge_model_path)
    counts = init_database(settings.database_path, Path(settings.seed_dir), encoder.encode_batch)
    logger.info("init_db_done", **counts)


if __name__ == "__main__":
    main()
