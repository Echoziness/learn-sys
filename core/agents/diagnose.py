"""学情诊断 Agent。仅访问 learner_profile 和 test_results——其余 state 字段不可访问。"""

import json

import structlog

from core.llm import LLMProvider
from core.state import AgentState, DiagnoseOutput

logger = structlog.get_logger()

DIAGNOSE_PROMPT = """你是一位学情诊断专家。根据学习者的背景信息和测试结果，完成以下任务：

1. 分析学习者的已有知识基础和技能盲区。
2. 为"大数据分析初级"方向，列出最需要学习的前 5 个知识点（用简洁的中文短语，如"SQL 查询基础""描述性统计""pandas 数据处理"；短语中不要使用引号、冒号等特殊符号）。
3. 输出一段 50 字以内的学习者画像摘要。
4. 评估学习者当前的整体水平：beginner（零基础或刚入门）、intermediate（有一定基础）、advanced（基础扎实）。判断依据是已有知识储备和背景经历，而非测试结果。

测试结果（可为空，为空则仅根据背景推断）：
{test_results}

学习者背景：
{profile}

严格按 JSON 格式输出：
{{"gaps": [...], "profile_summary": "...", "difficulty_level": "beginner|intermediate|advanced"}}"""


async def diagnose_node(
    state: AgentState, *, provider: LLMProvider, model: str | None = None
) -> dict:
    profile = state.get("learner_profile")
    output = await provider.chat_validated(
        [
            {
                "role": "user",
                "content": DIAGNOSE_PROMPT.format(
                    test_results=json.dumps(state.get("test_results", []), ensure_ascii=False),
                    profile=json.dumps(
                        profile.model_dump() if profile else {}, ensure_ascii=False
                    ),
                ),
            }
        ],
        schema=DiagnoseOutput,
        model=model,
    )
    logger.info("diagnose_done", gaps_count=len(output.gaps), summary=output.profile_summary[:40], level=output.difficulty_level)
    return {
        "gaps": output.gaps,
        "profile_summary": output.profile_summary,
        "difficulty_level": output.difficulty_level,
    }
