"""StateGraph 装配入口（组合根的一部分）。

依赖显式注入：provider / retriever / settings 由调用方构造传入，本模块无任何全局状态。
编译产物的缓存复用由调用方负责（FastAPI 生命周期或 CLI 单例），不在此处藏全局单例。

Phase 1: 线性流 diagnose → retrieve → generate → review → END
Phase 2: review 后接条件路由（pass → deliver / retry → generate / 超 3 轮 → 降级重组）
"""

import asyncio
from functools import partial

import structlog
from langgraph.graph import END, START, StateGraph

from core.agents import diagnose_node, generate_node, review_node
from core.config import Settings
from core.llm import LLMProvider
from core.retrieval import Retriever
from core.state import AgentState

logger = structlog.get_logger()


async def retrieve_node(
    state: AgentState, *, retriever: Retriever, top_k: int
) -> dict:
    """同步检索内核（sqlite + CPU 密集 encode）放到工作线程，避免冻结事件循环。"""
    gaps = state.get("gaps", [])
    result = await asyncio.to_thread(retriever.search_gaps, gaps, top_k)
    return {
        "retrieved_entries": result.entries,
        "uncovered_gaps": result.uncovered_gaps,
    }


def build_graph(settings: Settings, provider: LLMProvider, retriever: Retriever):
    g = StateGraph(AgentState)
    g.add_node("diagnose", partial(diagnose_node, provider=provider, model=settings.diagnose_model))
    g.add_node("retrieve", partial(retrieve_node, retriever=retriever, top_k=settings.retrieval_top_k))
    g.add_node("generate", partial(generate_node, provider=provider, model=settings.generate_model))
    g.add_node("review", partial(review_node, provider=provider, model=settings.review_model))

    g.add_edge(START, "diagnose")
    g.add_edge("diagnose", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "review")
    g.add_edge("review", END)

    compiled = g.compile()
    logger.info("graph_compiled", nodes=["diagnose", "retrieve", "generate", "review"])
    return compiled
