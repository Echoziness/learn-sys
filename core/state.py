"""AgentState：LangGraph 全局状态定义，Agent 间消息的唯一通道。

约定：
- LLM 输出必须在 agent 边界 parse 成下方 Pydantic 模型后再写入 state，禁止裸 dict 流动；
- review_history 使用 append reducer，裁决日志只增不改：每轮只 append 本轮新裁决的论断，
  论断的当前裁决 = 日志中该论断最新一条（latest_verdicts），全程可追溯；
- 各 agent 的上下文隔离由 graph 装配层保证（节点只提取职责内字段）。
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

from core.plan import KnowledgeEntry


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
    claim_type: Literal["core", "extension", "procedure_guide"] = "core"
    # core=条目覆盖层（严格证据链，NLI 逐字核对）；
    # extension=错因扩展层（仅重教轮出现，针对学生错因的应用级讲解，
    # 允许推导/示例，审核降为"概念一致 + 推导自洽"）；
    # procedure_guide=实操指南步骤（仅 procedure 条目，步骤+示例+检查点）


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
    anchor_entry: KnowledgeEntry  # 逐主题教学的锚定条目（当前主题本体），强制进上下文
    retrieved_entries: list[RetrievedEntry]
    uncovered_gaps: list[str]  # 检索置信度不足，生成时必须声明"知识库未覆盖"而非编造

    # generate
    outline: dict
    draft: list[DraftClaim]
    cited_entries: list[RetrievedEntry]
    retry_context: str  # 错因回流：上一轮**答错**的题目+作答+评估（extension 论断触发源）
    advance_hint: str  # 识别通过推进：上一轮 choice 答对（core 论断向应用推进，不触发 extension）
    taught_previously: list[str]  # 重教轮去重：之前各轮已教过的论断文本（禁止复读，重教必须给增量）

    # review：裁决日志追加式累积（每轮只 append 新裁决的论断）；feedback 是生成 Agent 下一轮的唯一反馈通道
    review_round: int
    review_history: Annotated[list[ReviewNote], operator.add]
    last_review_feedback: str
    # 定向改写通道（2026-08-26）：本轮被驳回论断清单（claim_index/text/verdict/reason/suggestion）
    # 非空时 generate 只重写这几条（保持 claim_index 原位替换），不再整稿重生成；
    # 每轮 review 覆写，重教轮新 invoke 天然为空。
    rejected_claims: list[dict]
