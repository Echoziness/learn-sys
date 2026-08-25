"""导出管线端到端：tmp DB 造会话数据 → 导出 → 同构自检（SeedEntry 可校验 = 可入库）。"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from scripts.export_packages import (
    collect_export_entries,
    collect_fail_material,
    persist_exported,
    validate_exported,
)
from scripts.init_db import SeedEntry, ensure_schema

from core.session import SessionStore

ENTRY_DICT = {
    "id": "BDA-SQL-001",
    "title": "SELECT 基础查询",
    "content": "SELECT 语句用于从数据库表中检索数据。基本语法为 SELECT 列名 FROM 表名。"
    "可以使用 * 选取所有列，使用 DISTINCT 去除重复行。",
}


class _Entry:
    id = "BDA-SQL-001"
    title = ENTRY_DICT["title"]
    content = ENTRY_DICT["content"]
    knowledge_type = "procedure"
    difficulty = 2
    prerequisites = ["BDA-DB-001"]
    keywords = ["SQL", "SELECT", "FROM", "查询"]
    source = "ISO/IEC 9075"


class FakeProvider:
    """distill 用 fake：返回固定误区（含一条跑题的，应被校验丢弃）。"""

    async def chat_validated(self, messages, schema, model=None, **kwargs):
        from core.agents.distill import DistillOutput

        return DistillOutput(
            pitfalls=[
                "常见误区：认为 SELECT 查询会修改表中数据；正确理解是 SELECT 只读取数据。",
                "量子纠缠是微观粒子的关联现象，与本主题完全无关。",
            ]
        )


@pytest.fixture()
def store(tmp_path):
    import sqlite_vec

    db_path = str(tmp_path / "knowledge.db")
    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    ensure_schema(db, 8)
    db.close()
    return SessionStore(db_path)


def _seed_session(store: SessionStore) -> str:
    sid = store.create_session("p01", {"background": {}, "mastery": {}, "style_tags": []})
    store.save_diagnosis(
        sid, gap_ids=["BDA-SQL-001"], difficulty_level="beginner",
        profile_summary="测试", plan={"topics": [], "uncovered_gaps": []},
    )
    # 一轮答错 + 一轮脚手架（distill 原料）
    store.save_round(
        sid, entry_id="BDA-SQL-001", round_no=1,
        question={"question_id": "q_BDA-SQL-001_r1_answer", "question_type": "answer",
                  "prompt": "如何查询所有列？", "options": [], "expected_label": ""},
        expected=["SELECT"], answer="用 SELECT 修改表数据",
        grade={"is_correct": False, "evaluation": "SELECT 是只读操作", "missed_requirements": []},
        decision="retry", mastery_after=0.0,
    )
    store.save_round(
        sid, entry_id="BDA-SQL-001", round_no=2,
        question={"question_id": "q_BDA-SQL-001_scaffold", "question_type": "choice",
                  "prompt": "脚手架题", "options": ["A. SELECT 只读取数据", "B. SELECT 会修改数据"],
                  "expected_label": "A"},
        expected=[], answer="A",
        grade={"is_correct": True, "evaluation": "", "missed_requirements": []},
        decision="retry", mastery_after=0.5,
    )
    store.upsert_package(
        sid, "p01", "BDA-SQL-001",
        lecture=[
            {"text": "SELECT 语句用于从数据库表中检索数据，配合 FROM 构成查询。",
             "evidence_ids": ["BDA-SQL-001"], "claim_type": "core", "round": 1},
            {"text": "使用 DISTINCT 可以去除查询结果中的重复行。",
             "evidence_ids": ["BDA-SQL-001"], "claim_type": "core", "round": 2},
        ],
        questions=[], practice=None, challenge=None, difficulty_tier="beginner",
    )
    store.finish_session(sid, status="finished")
    return sid


def test_collect_fail_material(store):
    sid = _seed_session(store)
    wrong, distractors = collect_fail_material(store, sid, "BDA-SQL-001")
    assert len(wrong) == 1 and "修改表数据" in wrong[0]["answer"]
    # 脚手架干扰项 = 非正确标签的选项
    assert distractors == ["B. SELECT 会修改数据"]


def test_export_end_to_end_validates_and_distills(store):
    sid = _seed_session(store)
    session = store.get_session(sid)
    assert session is not None
    exported = asyncio.run(
        collect_export_entries(store, session, [_Entry], provider=FakeProvider())  # type: ignore[arg-type]
    )
    assert len(exported) == 1
    item = exported[0]
    # distill 提炼的误区进 content，跑题条被丢弃
    assert "常见误区" in item["content"] and "只读" in item["content"]
    assert "量子纠缠" not in item["content"]
    # 同构自检零错误 + SeedEntry 可直接校验（= init_db 可入库）
    assert validate_exported(exported) == []
    SeedEntry.model_validate(item)
    # 序列化形态与 entries.jsonl 行格式一致（单行 JSON）
    line = json.dumps(item, ensure_ascii=False)
    SeedEntry.model_validate_json(line)


def test_persist_exported_roundtrip(store):
    """落库后可经 load_export_entries 读回（GET /exports 数据源），源条目 id 反推正确。"""
    sid = _seed_session(store)
    session = store.get_session(sid)
    assert session is not None
    exported = asyncio.run(
        collect_export_entries(store, session, [_Entry], provider=FakeProvider())  # type: ignore[arg-type]
    )
    persist_exported(store, sid, "p01", exported)
    loaded = store.load_export_entries(sid)
    assert len(loaded) == 1
    assert loaded[0]["id"] == "GEN-BDA-SQL-001-p01"
    assert loaded[0]["source_entry_id"] == "BDA-SQL-001"
    assert loaded[0]["content"] == exported[0]["content"]
    # 落库不污染同构体：entry_json 仍可直接过 SeedEntry 校验
    seed_shape = {k: v for k, v in loaded[0].items() if k not in ("source_entry_id", "exported_at")}
    SeedEntry.model_validate(seed_shape)
