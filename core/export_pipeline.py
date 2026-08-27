"""资源包条目化导出管线（2026-08-26 下沉自 scripts，CLI 与 Web 端点共用）。

产出物与知识库同一规范（SeedEntry schema 同构）：导出条目可被 init_db 原样
入库——"产出物可复用"的硬证明。进库的是知识本身：讲义论断（审核通过）直接
拼接；错题/脚手架/追问原料经 distill agent 提炼为"常见误区"知识段落进入 content。

自检：每条过同构校验（字段/枚举/难度区间）+ 关键词字符 ⊆ content（判分同语义）
+ id 唯一，任一失败抛 ExportValidationError（导出物必须天然满足知识库约束）。

core 不依赖 scripts（分层纪律）：校验模型在本模块镜像 SeedEntry 约束，
两处约束变更必须同步（见 scripts/init_db.SeedEntry）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from core.agents.distill import distill_pitfalls
from core.deliver import _lecture_to_knowledge, has_personal_reference, package_to_entry
from core.llm import LLMProvider
from core.plan import KnowledgeEntry
from core.session import SessionStore

logger = structlog.get_logger()

# 与 scripts/init_db.KnowledgeType 同集（镜像约束，变更需同步）
_KnowledgeType = Literal["memory", "concept", "procedure"]


class _ExportSchema(BaseModel):
    """SeedEntry 镜像（字段与约束一致，避免 core → scripts 反向依赖）。"""

    id: str
    knowledge_type: _KnowledgeType = "concept"
    title: str
    content: str
    prerequisites: list[str] = Field(default_factory=list)
    difficulty: int = Field(ge=1, le=5)
    keywords: list[str] = Field(default_factory=list)
    source: str = ""


class ExportValidationError(ValueError):
    """导出自检失败：条目不满足知识库同构约束。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


def claims_total_by_entry(store: SessionStore, session_id: str) -> dict[str, int]:
    """每条目讲义累积论断总数（审核通过率的分母）。

    以资源包讲义为权威口径（2026-08-27 修正）：讲义是跨轮追加合并的，
    而 teach_delivered 事件只含当轮交付论断——多轮重教会话里两者不等，
    旧口径会算出"通过 18/10"这类分子>分母的溯源失真。
    """
    totals: dict[str, int] = {}
    for pkg in store.load_packages(session_id):
        lecture = pkg.get("lecture") or []
        totals[pkg["entry_id"]] = len(lecture)
    return totals


def collect_fail_material(
    store: SessionStore, session_id: str, entry_id: str
) -> tuple[list[dict[str, str]], list[str]]:
    """distill 原料（三类困惑信号，对应三种增量来源）：
    1. 答错记录（题目/错答/遗漏）——认知偏差的直接证据；
    2. 脚手架干扰项——镜像的典型错误理解；
    3. 追问确认题题干与干扰项——学生主动暴露的困惑点（源条目表述不清的
       最强信号，2026-08-27 纳入；干扰项即 LLM 判定的候选误解）。

    评估文本（evaluation）不进原料：那是面向当前学生的第二人称个性化措辞，
    喂给 distill 会被复读到可复用知识里——事实原料只需题目/作答/遗漏。
    """
    wrong_records: list[dict[str, str]] = []
    scaffold_distractors: list[str] = []
    for r in store.load_rounds(session_id, entry_id):
        q = r.get("question") or {}
        grade = r.get("grade") or {}
        qid = q.get("question_id", "")
        # 困惑记录轮（answer 承载的是系统解答非学生作答）不进错题记录，
        # 其困惑信号由下方 _followup 分支单独采集（避免误当学生错答）
        if (
            r.get("answer") is not None
            and not grade.get("is_correct", False)
            and not qid.endswith("_followup")
        ):
            wrong_records.append(
                {
                    "prompt": q.get("prompt", ""),
                    "answer": r["answer"],
                    "missed": "；".join(grade.get("missed_requirements") or []),
                }
            )
        # 脚手架与追问同构：干扰项都是误解镜像，同为误区提炼原料；
        # 追问的题干额外采集——学生主动暴露的困惑点（源条目表述不清的强信号，
        # 2026-08-27 纳入；新式追问无选项，只贡献题干）。困惑记录的 answer 是系统解答，
        # 不作为学生作答采集。
        if qid.endswith("_scaffold") or qid.endswith("_followup"):
            if qid.endswith("_followup") and q.get("prompt"):
                wrong_records.append({"prompt": q["prompt"], "answer": "", "missed": ""})
            label = q.get("expected_label", "A")
            for opt in q.get("options", []):
                if not opt.startswith(f"{label}."):
                    scaffold_distractors.append(opt)
    return wrong_records[:6], scaffold_distractors[:6]  # 原料截断：误区提炼只需代表样本


def validate_exported(entries: list[dict[str, Any]]) -> list[str]:
    """同构自检：字段/枚举/难度区间校验 + 关键词字符 ⊆ content + 无学习者指涉 + id 唯一。

    学习者指涉检查是导出防线的最后一环（2026-08-27）：可复用知识必须与具体学习者无关，
    过滤层（package_to_entry）漏网的画像/会话特定指涉在此 fail-closed。
    """
    errors: list[str] = []
    seen: set[str] = set()
    for raw in entries:
        try:
            e = _ExportSchema.model_validate(raw)
        except Exception as exc:
            errors.append(f"{raw.get('id', '?')} schema 校验失败: {str(exc)[:120]}")
            continue
        if e.id in seen:
            errors.append(f"{e.id} id 重复")
        seen.add(e.id)
        chars = set(re.sub(r"\s+", "", e.content.lower()))
        for kw in e.keywords:
            if not set(re.sub(r"\s+", "", kw.lower())) <= chars:
                errors.append(f"{e.id} 关键词 {kw!r} 字符未全部出现在 content")
        if has_personal_reference(e.content) or has_personal_reference(e.title):
            errors.append(f"{e.id} content 含学习者指涉（可复用知识必须与学习者无关）")
    return errors


def persist_exported(
    store: SessionStore, session_id: str, learner_id: str, exported: list[dict[str, Any]]
) -> None:
    """导出产物落库（GET /exports 数据源）。源条目 id 由生成 id 规范反推。"""
    rows = [
        {
            **item,
            "source_entry_id": item["id"].removeprefix("GEN-").removesuffix(f"-{learner_id}"),
        }
        for item in exported
    ]
    store.save_export_entries(session_id, rows)


async def collect_export_entries(
    store: SessionStore,
    session: dict[str, Any],
    entries: list[KnowledgeEntry],
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """导出主流程（可测）：逐包 收集错题素材 → distill 提炼 → 条目化。"""
    sid: str = session["session_id"]
    level: str = session.get("difficulty_level") or "beginner"
    packages = store.load_packages(sid)
    totals = claims_total_by_entry(store, sid)
    by_id = {e.id: e for e in entries}

    exported: list[dict[str, Any]] = []
    for pkg in packages:
        source = by_id.get(pkg["entry_id"])
        if source is None:
            logger.warning("export_skip_unknown_entry", entry_id=pkg["entry_id"])
            continue
        wrong, distractors = collect_fail_material(store, sid, pkg["entry_id"])
        # 讲义锚点：知识化过滤后的论断作"正确理解"的取材源（序号即 evidence_ids 取值）
        taught_claims = _lecture_to_knowledge(
            [c for c in (pkg.get("lecture") or []) if isinstance(c, dict) and c.get("text")]
        )
        pitfalls = await distill_pitfalls(
            {
                "entry": {"id": source.id, "title": source.title, "content": source.content},
                "wrong_records": wrong,
                "scaffold_distractors": distractors,
                "taught_claims": taught_claims,
            },
            provider=provider,
            model=model,
        )
        item = package_to_entry(
            pkg,
            source,
            learner_id=session["learner_id"],
            difficulty_level=level,
            claims_total=totals.get(pkg["entry_id"], 0),
            pitfalls=pitfalls,
        )
        if item is None:
            logger.warning("export_skip_empty_lecture", entry_id=pkg["entry_id"])
            continue
        exported.append(item)
        logger.info(
            "export_entry",
            id=item["id"], content_len=len(item["content"]),
            keywords=len(item["keywords"]), pitfalls=len(pitfalls),
        )
    return exported


async def run_export(
    store: SessionStore,
    session: dict[str, Any],
    entries: list[KnowledgeEntry],
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """导出全流程：收集条目化 → 自检（失败抛 ExportValidationError）→ 落库 → 事件。"""
    sid = session["session_id"]
    exported = await collect_export_entries(
        store, session, entries, provider=provider, model=model
    )
    errors = validate_exported(exported)
    if errors:
        raise ExportValidationError(errors)
    persist_exported(store, sid, session["learner_id"], exported)
    await store.emit(
        sid,
        "packages_exported",
        {"entry_ids": [item["id"] for item in exported], "count": len(exported)},
    )
    return exported


def export_to_jsonl(exported: list[dict[str, Any]]) -> str:
    """导出条目序列化为 entries.jsonl 文本（交付物与下载端点同构）。"""
    return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in exported)


__all__ = [
    "ExportValidationError",
    "claims_total_by_entry",
    "collect_export_entries",
    "collect_fail_material",
    "export_to_jsonl",
    "persist_exported",
    "run_export",
    "validate_exported",
]
