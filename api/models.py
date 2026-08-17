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


__all__ = [
    "AnswerRequest",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "PlanTopicOut",
    "ProfileBackground",
    "SessionEventOut",
    "SessionListItemOut",
]
