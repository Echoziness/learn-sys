"""交付包生成器：源码归档 + 部署说明 + 测试数据包 + 清单校验。

产出（默认 dist/交付包/）：
├── 01-源码/learn-sys-源码.tar.gz     # git archive HEAD（含 data/seeds；不含模型/DB——均未入 git）
├── 02-部署说明.md                    # docs/部署说明.md 副本
├── 03-测试数据包/
│   ├── README.md                     # 三组会话选型说明 + 文件清单
│   ├── 01-知识库切片/                # entries.jsonl（与系统入库同源）
│   └── 02-会话示例/<learner>-<id8>/  # 每组：画像输入 → 协同决策中间数据 → 资源输出 → 三指标
└── MANIFEST.md                       # 全包清单 + 校验结果 + 模型打包指引

测试数据包选型（赛题要求 ≥2 组差异化，实用价值项要求 ≥3 组测试用例）：
默认自动选三组已完成会话——正向推进组（资源包最多）/ 回退博弈组（topic_regress
事件最多）/ 学不会组（资源包最少）；也可 --sessions 显式指定。

用法：
    uv run python scripts/pack_delivery.py                # 自动选型
    uv run python scripts/pack_delivery.py --sessions <id1>,<id2>,<id3>
    uv run python scripts/pack_delivery.py --zip-model    # 附带打包 4.5G 模型（耗时）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from core.session import SessionStore
from core.state import DraftClaim, ReviewNote
from evals.metrics import hallucination_rate, keyword_coverage, tier_match_rate

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "knowledge.db"
SEEDS_ENTRIES = ROOT / "data" / "seeds" / "bigdata-analysis" / "entries.jsonl"
DEPLOY_DOC = ROOT / "docs" / "部署说明.md"
BGE_DIR = ROOT / "data" / "bge-m3"

ROLE_LABEL = {
    "正向推进": "主题推进顺畅、资源包齐备的代表会话",
    "回退博弈": "含打回重写与连错回退——多智能体协同决策中间数据最完整",
    "学不会": "模拟学生全程未达标——系统不强产空洞资源的差异化决策样本",
}


def _fail(msg: str) -> NoReturn:
    print(f"[校验失败] {msg}", file=sys.stderr)
    sys.exit(1)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def archive_source(out_dir: Path) -> Path:
    """git archive HEAD：只含已追踪文件（模型/DB/评测结果未入 git，天然排除）。"""
    out = out_dir / "01-源码" / "learn-sys-源码.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "archive", "--format=tar.gz", "--prefix=learn-sys/", "HEAD", "-o", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return out


def select_sessions(conn: sqlite3.Connection, explicit: list[str] | None) -> list[tuple[str, str]]:
    """返回 [(session_id, 角色标签)]。显式指定时角色统一记为「指定」。"""
    if explicit:
        for sid in explicit:
            if conn.execute(
                "SELECT 1 FROM sessions WHERE session_id=? AND finished_at IS NOT NULL", (sid,)
            ).fetchone() is None:
                _fail(f"会话 {sid} 不存在或未完成（测试数据需完整闭环会话）")
        return [(sid, "指定") for sid in explicit]

    completed = "SELECT session_id FROM sessions WHERE finished_at IS NOT NULL"
    # ① 正向推进：资源包最多
    row = conn.execute(
        f"SELECT p.session_id, COUNT(*) c FROM resource_packages p "
        f"WHERE p.session_id IN ({completed}) GROUP BY p.session_id ORDER BY c DESC LIMIT 1"
    ).fetchone()
    if row is None:
        _fail("库中无带资源包的已完成会话，无法自动选型——请先跑评测或用 --sessions 指定")
    positive = row[0]
    # ② 回退博弈：含 topic_regress 事件且回退次数最多（排除①）
    row = conn.execute(
        f"SELECT e.session_id, COUNT(*) c FROM session_events e "
        f"WHERE e.event_type='topic_regress' AND e.session_id IN ({completed}) "
        f"AND e.session_id != ? GROUP BY e.session_id ORDER BY c DESC LIMIT 1",
        (positive,),
    ).fetchone()
    regress = row[0] if row else positive
    # ③ 学不会：已完成会话中资源包最少（排除①②）
    row = conn.execute(
        "SELECT s.session_id FROM sessions s WHERE s.finished_at IS NOT NULL "
        "AND s.session_id NOT IN (?, ?) "
        "ORDER BY (SELECT COUNT(*) FROM resource_packages p WHERE p.session_id = s.session_id) ASC "
        "LIMIT 1",
        (positive, regress),
    ).fetchone()
    struggling = row[0] if row else positive
    return [(positive, "正向推进"), (regress, "回退博弈"), (struggling, "学不会")]


def session_metrics(store: SessionStore, keywords: dict[str, list[str]], sid: str) -> dict:
    """三指标——与报告端点/批量评测同口径（claims+verdicts base offset 聚合）。"""
    drafts: list[DraftClaim] = []
    reviews: list[ReviewNote] = []
    for ev in store.load_events(sid, limit=1_000_000):
        if ev.event_type != "teach_delivered":
            continue
        base = len(drafts)
        for c in ev.payload.get("claims", []):
            drafts.append(
                DraftClaim(
                    claim_index=base + c["claim_index"],
                    text=c["text"],
                    evidence_ids=c.get("evidence_ids", []),
                    claim_type=c.get("claim_type", "core"),
                )
            )
        for i, verdict in ev.payload.get("verdicts", {}).items():
            reviews.append(ReviewNote(claim_index=base + int(i), verdict=verdict, reason="交付包聚合"))
    packages = store.load_packages(sid)
    pkg_keywords = {eid: kws for eid, kws in keywords.items() if eid in {p["entry_id"] for p in packages}}
    tier_rate, tier_matched, tier_total = tier_match_rate(packages)
    cov_rate, cov_hit, cov_total = keyword_coverage(packages, pkg_keywords)
    return {
        "hallucination_rate": round(hallucination_rate(drafts, reviews), 4),
        "claims_total": len(drafts),
        "tier_match": {"rate": round(tier_rate, 4), "matched": tier_matched, "total": tier_total},
        "keyword_coverage": {"rate": round(cov_rate, 4), "hit": cov_hit, "total": cov_total},
        "口径": "与批量评测（evals/metrics.py）逐组结果完全一致",
    }


def export_session(store: SessionStore, sid: str, role: str, dest: Path) -> dict:
    """单组完整输入输出：画像 → 中间数据 → 资源输出。返回摘要信息供 README 引用。"""
    dest.mkdir(parents=True, exist_ok=True)
    session = store.get_session(sid)
    assert session is not None

    # 输入：学习者画像（get_session 已解析 JSON）
    profile = {
        "learner_id": session["learner_id"],
        "difficulty_level": session["difficulty_level"],
        "profile": session["profile"],
        "gap_ids": session["gap_ids"],
        "plan": session["plan"],
    }
    (dest / "00-输入画像与诊断切片.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 中间数据：多智能体协同决策事件流（诊断/检索/生成/审核裁决/打回/判分/进退决策）
    events = store.load_events(sid, limit=1_000_000)
    with (dest / "01-协同决策中间数据-事件流.jsonl").open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(
                json.dumps(
                    {"seq": ev.seq, "event_type": ev.event_type, "payload": ev.payload},
                    ensure_ascii=False,
                )
                + "\n"
            )
    (dest / "02-出题判分轮次明细.json").write_text(
        json.dumps(store.load_rounds(sid), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dest / "03-掌握度快照轨迹.json").write_text(
        json.dumps(store.load_mastery_report(sid), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 输出：个性化资源（三形态）+ 条目化导出物
    packages = store.load_packages(sid)
    (dest / "04-个性化资源包-三形态.json").write_text(
        json.dumps(packages, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    exports = store.load_export_entries(sid)
    with (dest / "05-条目化导出-知识库同构条目.jsonl").open("w", encoding="utf-8") as f:
        for e in exports:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    return {
        "role": role,
        "learner_id": session["learner_id"],
        "difficulty_level": session["difficulty_level"] or "—",
        "topic_count": len(profile["plan"].get("topics", [])),
        "event_count": len(events),
        "regress_count": sum(1 for ev in events if ev.event_type == "topic_regress"),
        "package_count": len(packages),
        "export_count": len(exports),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成交付包（源码归档 + 测试数据包 + 清单校验）")
    parser.add_argument("--out", default=str(ROOT / "dist" / "交付包"), help="输出目录")
    parser.add_argument("--sessions", help="显式指定会话 ID（逗号分隔），跳过自动选型")
    parser.add_argument("--zip-model", action="store_true", help="同时打包 data/bge-m3（约 4.5G，耗时）")
    args = parser.parse_args()

    # ---------- 前置校验 ----------
    if not DB_PATH.exists():
        _fail("data/knowledge.db 不存在——先运行 scripts/init_db.py 并跑通至少一组会话")
    if not SEEDS_ENTRIES.exists():
        _fail(f"知识种子缺失：{SEEDS_ENTRIES}")
    if not DEPLOY_DOC.exists():
        _fail("docs/部署说明.md 不存在")
    bge_marker = BGE_DIR / "models--BAAI--bge-m3"
    if not bge_marker.exists():
        _fail("data/bge-m3/ 模型目录不完整（缺 models--BAAI--bge-m3）——交付包必须含模型")

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    store = SessionStore(str(DB_PATH))

    # ---------- 1. 源码归档 ----------
    src = archive_source(out)
    print(f"[1/4] 源码归档：{src.name}（{_human_size(src.stat().st_size)}）")

    # ---------- 2. 部署说明 ----------
    shutil.copy(DEPLOY_DOC, out / "02-部署说明.md")

    # ---------- 3. 测试数据包 ----------
    tdp = out / "03-测试数据包"
    (tdp / "01-知识库切片").mkdir(parents=True)
    shutil.copy(SEEDS_ENTRIES, tdp / "01-知识库切片" / "bigdata-analysis-entries.jsonl")

    explicit = [s.strip() for s in args.sessions.split(",")] if args.sessions else None
    picked = select_sessions(conn, explicit)
    keywords = {
        r["id"]: json.loads(r["keywords"] or "[]")
        for r in conn.execute("SELECT id, keywords FROM knowledge_entries")
    }
    summaries = []
    for sid, role in picked:
        learner = conn.execute("SELECT learner_id FROM sessions WHERE session_id=?", (sid,)).fetchone()[0]
        dest = tdp / "02-会话示例" / f"{learner}-{sid[:8]}"
        summary = export_session(store, sid, role, dest)
        summary["session_id"] = sid
        (dest / "06-三指标.json").write_text(
            json.dumps(session_metrics(store, keywords, sid), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summaries.append(summary)
        print(f"[2/4] 测试数据：{learner}（{role}）→ {dest.name}/")

    lines = [
        "# 测试数据包说明",
        "",
        f"生成时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "赛题要求：≥1 个垂直领域知识库切片 + ≥2 组差异化学习者初始学情数据源",
        "（含输入画像特征、多智能体协同决策中间数据、最终生成的个性化学习资源）。",
        "本包含 1 个知识库切片 + "
        f"{len(summaries)} 组完整输入输出示例（实用价值项要求 ≥3 组测试用例）。",
        "",
        "## 会话选型",
        "",
        "| 角色 | 学习者 | 层级 | 主题数 | 事件数 | 回退次数 | 资源包 | 导出条目 | 说明 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['role']} | {s['learner_id']} | {s['difficulty_level']} | {s['topic_count']} "
            f"| {s['event_count']} | {s['regress_count']} | {s['package_count']} "
            f"| {s['export_count']} | {ROLE_LABEL.get(s['role'], '显式指定')} |"
        )
    lines += [
        "",
        "## 每组目录结构",
        "",
        "```",
        "00-输入画像与诊断切片.json      # 输入：画像特征 + 诊断收敛 + 课程切片",
        "01-协同决策中间数据-事件流.jsonl # 中间数据：诊断/检索/生成/审核裁决/打回/判分/进退决策全事件",
        "02-出题判分轮次明细.json        # 中间数据：逐轮题目/作答/判分/决策",
        "03-掌握度快照轨迹.json          # 中间数据：掌握度随作答的演化",
        "04-个性化资源包-三形态.json     # 输出：讲义/分阶题/实操指南（含溯源链与难度层级）",
        "05-条目化导出-知识库同构条目.jsonl # 输出：资源喂回知识库的同构条目（产出物复用闭环）",
        "06-三指标.json                  # 本会话三指标（与批量评测同口径）",
        "```",
        "",
        "数据合规：画像为程序生成的合成数据，不含真实个人信息；交互记录仅本地存储。",
    ]
    (tdp / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- 4. 模型打包（可选）+ 清单 ----------
    model_note = "未打包（默认）。请手动压缩后随交付包提交："
    model_zip = out / "04-模型文件" / "bge-m3.zip"
    if args.zip_model:
        model_zip.parent.mkdir(parents=True, exist_ok=True)
        print("[3/4] 打包模型（约 4.5G，需数分钟）…")
        shutil.make_archive(
            str(model_zip.with_suffix("")), "zip", root_dir=BGE_DIR.parent, base_dir=BGE_DIR.name
        )
        model_note = f"已打包：{_human_size(model_zip.stat().st_size)}"
    else:
        print("[3/4] 跳过模型打包（--zip-model 可开启）")

    conn.close()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode().strip()
    bge_size = _human_size(sum(p.stat().st_size for p in BGE_DIR.rglob("*") if p.is_file()))
    manifest = [
        "# 交付包清单（MANIFEST）",
        "",
        f"生成时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        f"源码版本：{head}",
        "",
        "## 内容",
        "",
        f"- `01-源码/{src.name}`（{_human_size(src.stat().st_size)}，SHA-256 `{_sha256(src)[:16]}…`）",
        "- `02-部署说明.md` —— 本地部署步骤（环境要求/模型放置/.env 配置/验收）",
        f"- `03-测试数据包/` —— 知识库切片 + {len(summaries)} 组完整输入输出示例",
        f"- 模型文件（BGE-M3，解压后约 {bge_size}）：{model_note}",
        "",
        "```bash",
        "cd <源码根目录>/data && unzip <交付包>/04-模型文件/bge-m3.zip   # 得到 data/bge-m3/",
        "```",
        "",
        "## 提交前校验",
        "",
        "- [ ] 压缩包命名：学校—姓名—作品名称—联系电话（赛题第八条）",
        "- [ ] 模型目录已放入源码 `data/bge-m3/`（**模型未入 git，必须手动入包**）",
        "- [ ] 设计实现方案 / PPT / 10 分钟演示视频 / 报名表（盖章扫描件）已随包",
        "- [ ] 干净环境按《部署说明》走通全流程",
    ]
    (out / "MANIFEST.md").write_text("\n".join(manifest) + "\n", encoding="utf-8")

    print(f"[4/4] 完成：{out}")
    picked_desc = ", ".join(f"{s['learner_id']}({s['role']})" for s in summaries)
    print(f"      测试数据 {len(summaries)} 组：{picked_desc}")


if __name__ == "__main__":
    main()
