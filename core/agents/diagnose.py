"""学情诊断 Agent。仅访问 learner_profile 和 test_results——其余 state 字段不可访问。"""

import json

import structlog

from core.llm import LLMProvider
from core.state import AgentState, DiagnoseOutput

logger = structlog.get_logger()

DIAGNOSE_PROMPT = """你是一位学情诊断专家。根据学习者的背景信息和测试结果，完成以下任务：

1. 分析学习者的已有知识基础和技能盲区。
2. 从下方的【可用知识点目录】中，选出该学习者最需要学习的前 5 个知识点，
   把其 ID 写入 gap_ids（只允许选目录中存在的 ID，禁止编造）；
   若某盲区目录里没有对应条目，用一句简洁中文短语写入 gaps（可留空）。
3. 输出一段 50 字以内的学习者画像摘要。
4. 评估学习者当前的整体水平：beginner（零基础或刚入门）、intermediate（有一定基础）、
   advanced（基础扎实）。判断依据是已有知识储备和背景经历，而非测试结果。

【可用知识点目录】（id = 标题）：
{catalog}

测试结果（可为空，为空则仅根据背景推断）：
{test_results}

学习者背景：
{profile}

严格按 JSON 格式输出：
{{"gap_ids": [...], "gaps": [...], "profile_summary": "...",
  "difficulty_level": "beginner|intermediate|advanced"}}"""


async def diagnose_node(
    state: AgentState,
    *,
    provider: LLMProvider,
    model: str | None = None,
    entry_catalog: list[dict] | None = None,
) -> dict:
    profile = state.get("learner_profile")
    catalog_lines = (
        "\n".join(f"{e['id']} = {e['title']}" for e in entry_catalog)
        if entry_catalog
        else "（未提供目录，gap_ids 输出空列表即可）"
    )
    output = await provider.chat_validated(
        [
            {
                "role": "user",
                "content": DIAGNOSE_PROMPT.format(
                    catalog=catalog_lines,
                    test_results=json.dumps(state.get("test_results", []), ensure_ascii=False),
                    profile=json.dumps(
                        profile.model_dump() if profile else {}, ensure_ascii=False
                    ),
                ),
            }
        ],
        schema=DiagnoseOutput,
        model=model,
        temperature=0.0,  # 诊断必须可复现：同一画像两次诊断应产出一致 gap_ids
    )
    logger.info(
        "diagnose_done",
        gap_ids_count=len(output.gap_ids),
        gaps_count=len(output.gaps),
        summary=output.profile_summary[:40],
        level=output.difficulty_level,
    )
    return {
        "gap_ids": output.gap_ids,
        "gaps": output.gaps,
        "profile_summary": output.profile_summary,
        "difficulty_level": output.difficulty_level,
    }
