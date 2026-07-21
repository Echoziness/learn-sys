"""AgentState: LangGraph 全局状态定义。所有 Agent 通过此 schema 交换数据。"""

from __future__ import annotations
from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages
from pydantic import BaseModel


class LearnerProfile(BaseModel):
    background: dict
    mastery: dict       # {entry_id: 0.0-1.0}
    style_tags: list[str]


class RetrievedEntry(BaseModel):
    id: str
    title: str
    content: str
    score: float


class DraftClaim(BaseModel):
    claim_index: int
    text: str
    evidence_ids: list[str]


class ReviewNote(BaseModel):
    claim_index: int
    verdict: Literal["supported", "partially_supported", "unsupported"]
    reason: str


class AgentState(TypedDict, total=False):
    learner_id: str
    learner_profile: dict
    test_results: list[dict]

    profile_summary: str
    gaps: list[str]
    outline: dict

    retrieved_entries: list[dict]
    draft: list[dict]
    cited_entries: list[dict]

    review_round: int
    review_history: list[dict]
    last_review_feedback: str

    final_resources: dict
    assessment_results: dict
