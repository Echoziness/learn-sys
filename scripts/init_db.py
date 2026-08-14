#!/usr/bin/env python3
"""初始化知识库：从 data/seeds/ 加载条目与画像 → 建表 → upsert → 重建 FTS → 重算 embedding。

幂等设计，可反复运行：条目按 id upsert（保留 rowid），向量按 rowid 删除重写，FTS 全量重建。
数据与代码分离——条目维护只需编辑 data/seeds/<domain>/entries.jsonl，无需触碰本脚本。
"""

import json
import sqlite3
import struct
from collections.abc import Callable
from enum import StrEnum
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
        knowledge_type TEXT NOT NULL DEFAULT 'concept'
                    CHECK(knowledge_type IN ('memory', 'concept', 'procedure')),
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

    -- ============ 会话层（W1，设计见 docs/架构设计文档.md §3.2） ============

    -- 会话元数据：画像快照 + 诊断结果 + 切片
    CREATE TABLE IF NOT EXISTS sessions (
        session_id      TEXT PRIMARY KEY,
        learner_id      TEXT NOT NULL,
        profile_json    TEXT NOT NULL,           -- 输入画像完整快照
        gap_ids_json    TEXT,                    -- 诊断收敛的本体条目 ID
        difficulty_level TEXT,                   -- beginner/intermediate/advanced
        profile_summary TEXT,
        plan_json       TEXT,                    -- 切片结果（含前置链）
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TEXT NOT NULL,
        finished_at     TEXT
    );

    -- 统一事件流：裁判面渲染协议 + 回放媒体流 + 审计日志（一表三用）
    CREATE TABLE IF NOT EXISTS session_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        seq         INTEGER NOT NULL,            -- 会话内单调递增，回放排序键
        event_type  TEXT NOT NULL,              -- 协议见架构文档 §4
        payload_json TEXT NOT NULL,             -- 自包含：前端仅凭 payload 可渲染
        created_at  TEXT NOT NULL,
        UNIQUE(session_id, seq)
    );
    CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id, seq);

    -- 教学轮快照：题目/作答/判分/决策的结构化中间数据
    CREATE TABLE IF NOT EXISTS topic_rounds (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        entry_id    TEXT NOT NULL,
        round_no    INTEGER NOT NULL,
        question_json TEXT,                      -- 题目（题型/题干/选项）
        expected_json TEXT,                      -- 判分要点（不进学生视野）
        answer_text TEXT,
        grade_json  TEXT,                        -- 覆盖率 + verdict + evaluation + missed
        decision    TEXT,                        -- advance/retry/regress/scaffold
        mastery_after REAL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rounds_session ON topic_rounds(session_id, entry_id);

    -- 掌握度历史：报告曲线与跨会话延续
    CREATE TABLE IF NOT EXISTS mastery_snapshots (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        learner_id  TEXT NOT NULL,
        entry_id    TEXT NOT NULL,
        round_no    INTEGER NOT NULL,
        correctness INTEGER NOT NULL,            -- 0/1（脚手架答对不写入）
        mastery_after REAL NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_mastery_session ON mastery_snapshots(session_id, entry_id);

    -- 资源包：三形态资源 + 溯源链（赛题主交付物）
    CREATE TABLE IF NOT EXISTS resource_packages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        learner_id  TEXT NOT NULL,
        entry_id    TEXT NOT NULL,
        lecture_json TEXT,                       -- 讲义：审核通过论断
        questions_json TEXT,                     -- 分阶题：choice/scaffold/answer 归档
        practice_json TEXT,                      -- 实操指南（procedure 条目）
        challenge_json TEXT,                     -- 进阶挑战任务（mastery≥0.85）
        difficulty_tier TEXT NOT NULL,           -- 资源难度层级（适配率指标输入）
        created_at  TEXT NOT NULL,
        UNIQUE(session_id, entry_id)
    );
"""


class KnowledgeType(StrEnum):
    """知识条目类型——描述知识本体（影响教学方式与实操指南生成），不绑定题型（题型由掌握度驱动）。"""

    memory = "memory"  # 事实/定义/术语
    concept = "concept"  # 需理解的概念与关系
    procedure = "procedure"  # 可操作的步骤技能（教学时产出实操指南）


class SeedEntry(BaseModel):
    id: str
    knowledge_type: KnowledgeType = KnowledgeType.concept
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
    migrate_knowledge_type(db)


def migrate_knowledge_type(db: sqlite3.Connection) -> None:
    """旧库（无 knowledge_type 列）幂等补列。选 ALTER TABLE 而非删库重建：
    保留运行时数据（画像/会话记录），不动 rowid → FTS/vec 外部表行对齐不受影响。
    列已存在时零操作，反复运行安全。"""
    cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)")}
    if "knowledge_type" not in cols:
        db.execute(
            "ALTER TABLE knowledge_entries ADD COLUMN knowledge_type TEXT "
            "NOT NULL DEFAULT 'concept' CHECK(knowledge_type IN ('memory', 'concept', 'procedure'))"
        )


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
               (id, domain, knowledge_type, title, content, prerequisites, difficulty, keywords, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 domain=excluded.domain, knowledge_type=excluded.knowledge_type,
                 title=excluded.title, content=excluded.content,
                 prerequisites=excluded.prerequisites, difficulty=excluded.difficulty,
                 keywords=excluded.keywords, source=excluded.source,
                 updated_at=datetime('now')""",
            (
                e.id, domain, e.knowledge_type.value, e.title, e.content,
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
    db.execute(
        """INSERT INTO learner_profiles(learner_id, background, mastery, style_tags)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(learner_id) DO UPDATE SET
             background=excluded.background, mastery=excluded.mastery,
             style_tags=excluded.style_tags, updated_at=datetime('now')""",
        (
            profile.learner_id,
            json.dumps(profile.background, ensure_ascii=False),
            json.dumps(profile.mastery, ensure_ascii=False),
            json.dumps(profile.style_tags, ensure_ascii=False),
        ),
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
