"""StateGraph 装配入口（组合根的一部分）。

依赖显式注入：provider / retriever / settings 由调用方构造传入，本模块无任何全局状态。
编译产物的缓存复用由调用方负责（FastAPI 生命周期或 CLI 单例），不在此处藏全局单例。

图结构（2026-07-22 会话化改造）：
- 教学子图（build_teach_graph）：retrieve → generate → review → END，
  输入 gaps=[当前主题]，CLI 组合根按 plan 的切片逐个调用——内循环
  （出题→判分→进/停/退）是确定性纯函数（core/assess、core/mastery），
  由交互层驱动，不进图（等学生输入不是图的职责）。
- diagnose 不进图：一次会话只诊断一次，由 CLI 直接调 diagnose_node。

课程切片原则：课程本体静态、切片由确定性算法推导、交流只约束教学执行层。
"""

import asyncio
from functools import partial

import structlog
from langgraph.graph import END, START, StateGraph

from core.agents import generate_node, review_node
from core.config import Settings
from core.llm import LLMProvider
from core.retrieval import Retriever
from core.state import AgentState

logger = structlog.get_logger()

# 难度闸门：初学者只看到难度 1-2 的条目，中级 1-3，高级无限制
DIFFICULTY_CAP: dict[str, int] = {"beginner": 2, "intermediate": 3, "advanced": 5}


async def retrieve_node(
    state: AgentState, *, retriever: Retriever, top_k: int
) -> dict:
    gaps = state.get("gaps", [])
    level = state.get("difficulty_level", "beginner")
    cap = DIFFICULTY_CAP.get(level, 2)
    result = await asyncio.to_thread(retriever.search_gaps, gaps, top_k, max_difficulty=cap)
    return {
        "retrieved_entries": result.entries,
        "uncovered_gaps": result.uncovered_gaps,
    }


def build_teach_graph(settings: Settings, provider: LLMProvider, retriever: Retriever):
    """主题教学子图：检索 → 生成 → 审核 → END。

    输入 state 须含 gaps（当前主题标题）、difficulty_level；输出 draft /
    review_history / retrieved_entries。调用方可反复 invoke 以重教（retry）。
    """
    g = StateGraph(AgentState)
    g.add_node("retrieve", partial(retrieve_node, retriever=retriever, top_k=settings.retrieval_top_k))
    g.add_node("generate", partial(generate_node, provider=provider, model=settings.generate_model))
    g.add_node("review", partial(review_node, provider=provider, model=settings.review_model))

    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "review")
    g.add_edge("review", END)

    compiled = g.compile()
    logger.info("teach_graph_compiled", nodes=["retrieve", "generate", "review"])
    return compiled
