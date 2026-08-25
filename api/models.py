"""API schema（Pydantic）——请求/响应模型。薄层：只做序列化，不含业务。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileBackground(BaseModel):
    education: str = ""
    major: str = ""
    goal: str = ""
    experience: str = ""


class CreateSessionRequest(BaseModel):
    learner_id: str = Field(min_length=1, max_length=64)
    background: ProfileBackground
    style_tags: list[str] = Field(default_factory=list)
    mastery: dict[str, float] = Field(default_factory=dict)


class PlanTopicOut(BaseModel):
    entry_id: str
    title: str
    order: int
    target: bool


class CreateSessionResponse(BaseModel):
    session_id: str
    learner_id: str
    difficulty_level: str
    profile_summary: str
    gap_ids: list[str]
    topics: list[PlanTopicOut]
    uncovered_gaps: list[str]


class AnswerRequest(BaseModel):
    entry_id: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)


class SessionEventOut(BaseModel):
    seq: int
    event_type: str
    payload: dict
    created_at: str


class SessionListItemOut(BaseModel):
    session_id: str
    learner_id: str
    difficulty_level: str | None = None
    status: str
    created_at: str
    finished_at: str | None = None
    topic_count: int = 0
    event_count: int = 0
    package_count: int = 0


class ExportedEntryOut(BaseModel):
    """条目化导出产物（知识库同构，FR-23）：字段与 SeedEntry 一致 + 溯源与导出时间。"""

    id: str
    source_entry_id: str = ""
    knowledge_type: str = "concept"
    title: str
    content: str
    prerequisites: list[str] = Field(default_factory=list)
    difficulty: int = 1
    keywords: list[str] = Field(default_factory=list)
    source: str = ""
    exported_at: str = ""


class AggregatedPackageOut(BaseModel):
    """跨会话聚合的资源包（资源库页面）：包字段 + 来源会话状态（会话已删时为 None）。"""

    session_id: str
    learner_id: str
    entry_id: str
    lecture: list[dict] = Field(default_factory=list)
    questions: list[dict] = Field(default_factory=list)
    practice: dict | None = None
    challenge: dict | None = None
    difficulty_tier: str
    created_at: str
    session_status: str | None = None


class AggregatedExportEntryOut(ExportedEntryOut):
    """跨会话聚合的导出条目：条目字段 + 来源会话信息。"""

    session_id: str = ""
    learner_id: str | None = None
    session_status: str | None = None


class ResourcesAggregateOut(BaseModel):
    packages: list[AggregatedPackageOut] = Field(default_factory=list)
    exports: list[AggregatedExportEntryOut] = Field(default_factory=list)


class DeleteSessionOut(BaseModel):
    session_id: str
    deleted: dict[str, int]
    kept_packages: bool
    kept_exports: bool


__all__ = [
    "AggregatedExportEntryOut",
    "AggregatedPackageOut",
    "AnswerRequest",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "DeleteSessionOut",
    "ExportedEntryOut",
    "PlanTopicOut",
    "ProfileBackground",
    "ResourcesAggregateOut",
    "SessionEventOut",
    "SessionListItemOut",
]
