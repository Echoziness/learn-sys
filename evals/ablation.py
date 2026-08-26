"""审核消融聚合：无回路「裸幻觉率」vs 有回路交付质量的两层对照。

消融口径（对照组天然内嵌于事件流，无需关闭审核重跑系统）：
- 基线（无回路）：review_done 事件 review_round=1 的裁决 = 初稿第一审——
  若系统没有打回重写机制，交付的讲义就是这份裁决对应的内容
- 现实（有回路）：teach_delivered 事件的最终裁决 = 打回重写后实际交付的质量
两层之差 = 打回回路净挽救的幻觉论断数。赛题「技术创新性」评分的核心论据。

最终层口径与批量评测（evals/metrics.py）一致：无裁决视为 unsupported（fail-closed）。

用法：
    uv run python evals/ablation.py          # 全库聚合，产出 evals/results/ablation.json
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "knowledge.db"
OUT_PATH = ROOT / "evals" / "results" / "ablation.json"
PROFILES_DIR = ROOT / "evals" / "profiles"


def _unsupported_in_verdicts(claims: list[dict], verdicts: dict[str, str]) -> int:
    """fail-closed：与 metrics.py 同语义——无裁决的论断计入 unsupported。"""
    count = 0
    for c in claims:
        verdict = verdicts.get(str(c.get("claim_index")))
        if verdict != "supported" and verdict != "partially_supported":
            count += 1
    return count


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 只聚合 50 组评测画像对应的已完成会话：排除开发/冒烟会话与跑批失败残留的
    # 未结束会话（如 p28 首次失败会话），与评测归档同口径；一画像一会话，重复即报错
    eval_learners = {p.stem for p in PROFILES_DIR.glob("*.json")}
    sessions = conn.execute(
        "SELECT session_id, learner_id FROM sessions "
        "WHERE finished_at IS NOT NULL ORDER BY created_at, rowid"
    ).fetchall()
    sessions = [s for s in sessions if s["learner_id"] in eval_learners]
    learners = [s["learner_id"] for s in sessions]
    if len(learners) != len(set(learners)):
        dup = {x for x in learners if learners.count(x) > 1}
        raise RuntimeError(f"同一画像存在多个已完成会话，需人工核对：{sorted(dup)}")

    per_session = []
    base_claims = base_unsup = 0
    final_claims = final_unsup = 0
    rewrite_rounds = 0
    for s in sessions:
        sid = s["session_id"]
        sb_claims = sb_unsup = sf_claims = sf_unsup = 0
        rows = conn.execute(
            "SELECT event_type, payload_json FROM session_events "
            "WHERE session_id=? ORDER BY seq",
            (sid,),
        ).fetchall()
        for r in rows:
            p = json.loads(r["payload_json"])
            if r["event_type"] == "review_done":
                if p.get("review_round") == 1:
                    verdicts = p.get("verdicts", [])
                    sb_claims += len(verdicts)
                    sb_unsup += sum(1 for v in verdicts if v.get("verdict") == "unsupported")
                elif p.get("review_round", 0) >= 2:
                    rewrite_rounds += 1
            elif r["event_type"] == "teach_delivered":
                claims = p.get("claims", [])
                sf_claims += len(claims)
                sf_unsup += _unsupported_in_verdicts(claims, p.get("verdicts", {}))
        base_claims += sb_claims
        base_unsup += sb_unsup
        final_claims += sf_claims
        final_unsup += sf_unsup
        per_session.append(
            {
                "learner_id": s["learner_id"],
                "baseline": {"claims": sb_claims, "unsupported": sb_unsup},
                "final": {"claims": sf_claims, "unsupported": sf_unsup},
            }
        )
    conn.close()

    base_rate = base_unsup / base_claims if base_claims else 0.0
    final_rate = final_unsup / final_claims if final_claims else 0.0
    report = {
        "sessions": len(sessions),
        "rewrite_rounds": rewrite_rounds,
        "baseline_no_loop": {
            "claims": base_claims,
            "unsupported": base_unsup,
            "hallucination_rate": round(base_rate, 4),
        },
        "final_with_loop": {
            "claims": final_claims,
            "unsupported": final_unsup,
            "hallucination_rate": round(final_rate, 4),
        },
        "saved_unsupported_claims": base_unsup - final_unsup,
        "relative_reduction": round((base_rate - final_rate) / base_rate, 4) if base_rate else 0.0,
        "口径": (
            "基线 = review_done review_round=1 首轮裁决（无打回回路时的交付内容）；"
            "最终 = teach_delivered 裁决（与 evals/metrics.py 同源，无裁决计 unsupported）"
        ),
        "per_session": per_session,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"会话数：{len(sessions)} · 打回重写轮次：{rewrite_rounds}")
    print(f"基线（无回路，首轮裁决）：{base_unsup}/{base_claims} = {base_rate:.2%}")
    print(f"最终（有回路，交付裁决）：{final_unsup}/{final_claims} = {final_rate:.2%}")
    print(
        f"回路净挽救：{base_unsup - final_unsup} 条幻觉论断"
        f"（相对降幅 {((base_rate - final_rate) / base_rate if base_rate else 0):.1%}）"
    )
    print(f"已写入 {OUT_PATH}")


if __name__ == "__main__":
    main()
