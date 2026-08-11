"""AgentState：LangGraph 全局状态定义，Agent 间消息的唯一通道。

约定：
- LLM 输出必须在 agent 边界 parse 成下方 Pydantic 模型后再写入 state，禁止裸 dict 流动；
- review_history 使用 append reducer，辩论链只增不改，全程可追溯；
- 各 agent 的上下文隔离由 graph 装配层保证（节点只提取职责内字段）。
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field


class LearnerProfile(BaseModel):
    background: dict
    mastery: dict[str, float] = Field(default_factory=dict)  # {entry_id: 0.0-1.0}
    style_tags: list[str] = Field(default_factory=list)


class RetrievedEntry(BaseModel):
    id: str
    title: str
    content: str
    score: float


class DraftClaim(BaseModel):
    claim_index: int
    text: str
    evidence_ids: list[str] = Field(min_length=1)  # 生产约束：无证据的论断在 schema 层即拒绝


class ReviewNote(BaseModel):
    claim_index: int
    verdict: Literal["supported", "partially_supported", "unsupported"]
    reason: str
    suggestion: str | None = None  # 打回时的修改建议，供生成 Agent 逐条回应


class DiagnoseOutput(BaseModel):
    gaps: list[str] = Field(default_factory=list)  # 自由文本盲区（备选，供展示）
    gap_ids: list[str] = Field(default_factory=list)  # 收敛到知识本体的条目 ID（切片依据）
    profile_summary: str
    difficulty_level: Literal["beginner", "intermediate", "advanced"]


class GenerateOutput(BaseModel):
    draft: list[DraftClaim]


class ReviewOutput(BaseModel):
    reviews: list[ReviewNote]


class AgentState(TypedDict, total=False):
    learner_id: str
    learner_profile: LearnerProfile
    test_results: list[dict]

    # diagnose
    profile_summary: str
    gaps: list[str]
    difficulty_level: str  # beginner / intermediate / advanced——检索难度闸门 + 生成提示强度

    # retrieve
    retrieved_entries: list[RetrievedEntry]
    uncovered_gaps: list[str]  # 检索置信度不足，生成时必须声明"知识库未覆盖"而非编造

    # generate
    outline: dict
    draft: list[DraftClaim]
    cited_entries: list[RetrievedEntry]

    # review：辩论链追加式累积；feedback 是生成 Agent 下一轮的唯一反馈通道
    review_round: int
    review_history: Annotated[list[ReviewNote], operator.add]
    last_review_feedback: str

    final_resources: dict
    assessment_results: dict
