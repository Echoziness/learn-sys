"""CLI 入口。读取 test1 画像 → 跑 graph → 输出结果。"""

import asyncio, json, sys, structlog
from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(line_buffering=True)

from core.graph import build_graph

logger = structlog.get_logger()


async def main():
    profile_input = {
        "learner_id": "test1",
        "learner_profile": {
            "education": "本科大二",
            "major": "机械工程",
            "goal": "转行数据分析",
            "experience": "学过 C 语言基础，会用 Excel 做简单统计，未接触过数据库和 Python"
        },
        "test_results": [],
        "review_round": 0,
        "review_history": [],
    }

    print("=" * 60)
    print("学情画像输入")
    print("=" * 60)
    print(json.dumps(profile_input["learner_profile"], ensure_ascii=False, indent=2))

    graph = build_graph()
    print("\n⏳ 正在执行 diagnose → retrieve → generate → review ...\n")

    final_state = await graph.ainvoke(profile_input)

    print("=" * 60)
    print("诊断结果")
    print("=" * 60)
    print(f"画像摘要: {final_state.get('profile_summary', 'N/A')}")
    print(f"知识盲区: {final_state.get('gaps', [])}")

    print("\n" + "=" * 60)
    print(f"检索条目（{len(final_state.get('retrieved_entries', []))} 条）")
    print("=" * 60)
    for e in final_state.get("retrieved_entries", []):
        print(f"  [{e['id']}] {e['title']} (score={e.get('score', 0):.3f})")

    print("\n" + "=" * 60)
    print(f"生成稿（{len(final_state.get('draft', []))} 条论断）")
    print("=" * 60)
    for claim in final_state.get("draft", []):
        eids = ", ".join(claim.get("evidence_ids", []))
        print(f"  [{claim['claim_index']}] {claim['text']}")
        print(f"      evidence: {eids}")

    print("\n" + "=" * 60)
    print(f"审核结果（{len(final_state.get('review_history', []))} 条裁决）")
    print("=" * 60)
    for note in final_state.get("review_history", []):
        symbol = {"supported": "✅", "partially_supported": "⚠️", "unsupported": "❌"}.get(note["verdict"], "?")
        print(f"  {symbol} [{note['claim_index']}] {note['verdict']}: {note['reason']}")

    unsupported_count = len([n for n in final_state.get("review_history", []) if n["verdict"] == "unsupported"])
    total_count = len(final_state.get("review_history", []))
    if total_count > 0:
        hallucination_rate = unsupported_count / total_count * 100
        print(f"\n幻觉率: {unsupported_count}/{total_count} = {hallucination_rate:.1f}%")

    print("\n✅ CLI 流程完成")


if __name__ == "__main__":
    asyncio.run(main())
