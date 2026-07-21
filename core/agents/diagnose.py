"""学情诊断 Agent。仅访问 learner_profile 和 test_results——其余 state 字段不可访问。"""

import json, structlog
from core.llm import provider, resolve_model

logger = structlog.get_logger()

DIAGNOSE_PROMPT = """你是一位学情诊断专家。根据学习者的背景信息和测试结果，完成以下任务：

1. 分析学习者的已有知识基础和技能盲区。
2. 为"大数据分析初级"方向，列出最需要学习的前 5 个知识点（用简洁的中文短语，如"SQL 查询基础""描述性统计""pandas 数据处理"）。
3. 输出一段 50 字以内的学习者画像摘要。

测试结果（可为空，为空则仅根据背景推断）：
{test_results}

学习者背景：
{profile}

严格按 JSON 格式输出：{{"gaps": [...], "profile_summary": "..."}}"""


async def _diagnose_inner(learner_profile: dict, test_results: list[dict]) -> dict:
    profile = json.dumps(learner_profile, ensure_ascii=False)
    results = json.dumps(test_results, ensure_ascii=False)

    raw = await provider.chat_json([{"role": "user", "content": DIAGNOSE_PROMPT.format(
        test_results=results, profile=profile
    )}], model=resolve_model("DIAGNOSE_MODEL"))
    result = json.loads(raw)
    gaps = result.get("gaps", [])
    summary = result.get("profile_summary", "")

    logger.info("diagnose_done", gaps_count=len(gaps), summary=summary[:40])
    return {"gaps": gaps, "profile_summary": summary}


async def diagnose_node(state: dict) -> dict:
    return await _diagnose_inner(
        learner_profile=state.get("learner_profile", {}),
        test_results=state.get("test_results", []),
    )
