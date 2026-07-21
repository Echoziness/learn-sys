"""StateGraph 装配入口。编译一次，运行时复用。

Phase 1: 线性流（diagnose → retrieve → generate → review → END）
Phase 2: 加条件路由（review 根据审核结果 → pass/deliver 或 retry/generate, ≤3 轮）"""

import structlog
from langgraph.graph import StateGraph, START, END
from core.state import AgentState
from core.agents import diagnose_node, generate_node, review_node
from core.retrieval import hybrid_search

logger = structlog.get_logger()


async def retrieve_node(state: AgentState) -> dict:
    gaps = state.get("gaps", [])
    seen: set[str] = set()
    entries: list[dict] = []
    for query in gaps:
        for r in hybrid_search(query, top_k=3):
            if r["id"] not in seen:
                seen.add(r["id"])
                entries.append(r)
    logger.info("retrieve_done", queries=len(gaps), entries=len(entries))
    return {"retrieved_entries": entries}


# Phase 2 路由函数（当前未使用，Phase 2 接入）
# def route_after_review(state: AgentState) -> str:
#     review_history = state.get("review_history", [])
#     has_unsupported = any(r["verdict"] == "unsupported" for r in review_history)
#     round_num = state.get("review_round", 0)
#     if not has_unsupported:
#         return "deliver"
#     if round_num >= 3:
#         return "degrade"
#     return "retry"


_graph = None


def build_graph():
    global _graph
    if _graph is not None:
        return _graph

    g = StateGraph(AgentState)
    g.add_node("diagnose", diagnose_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("review", review_node)

    # Phase 1 线性流
    g.add_edge(START, "diagnose")
    g.add_edge("diagnose", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "review")
    g.add_edge("review", END)

    # Phase 2 替换为条件路由：
    # g.add_conditional_edges("review", route_after_review, {
    #     "deliver": END,
    #     "retry": "generate",
    #     "degrade": END,
    # })

    _graph = g.compile()
    logger.info("graph_compiled", nodes=["diagnose", "retrieve", "generate", "review"])
    return _graph
