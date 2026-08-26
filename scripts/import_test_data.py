"""测试数据导入器：将交付包中的会话数据文件写入数据库，打通无 LLM 回放。

交付包里的测试数据包是文件形态（事件流 jsonl + 轮次 json + 掌握度 json + 资源包 json），
本脚本将它们灌入 SQLite，使前端会话列表/回放/报告页可直接浏览——无需 LLM key 即可演示。

数据文件结构（每组会话目录）：
    00-输入画像与诊断切片.json      → sessions + learners/learner_profiles
    01-协同决策中间数据-事件流.jsonl → session_events
    02-出题判分轮次明细.json        → topic_rounds
    03-掌握度快照轨迹.json          → mastery_snapshots
    04-个性化资源包-三形态.json     → resource_packages

用法：
    uv run python scripts/import_test_data.py
    uv run python scripts/import_test_data.py --db data/knowledge.db --data-dir <会话示例目录>

设计要点：
- session_id 以数据文件内记录的 32 位 hash 为准，不用目录名的短 hash；
- 事件流的 jsonl 没有时间戳，用轮次明细里第一条的 created_at 做起点、按 seq 秒展开；
- 会话状态置为 finished（数据包里都是完整会话，末事件为 session_end）；
- 幂等：同 session_id 先删后写，可反复执行。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "knowledge.db"


def _parse_iso(s: str) -> str:
    """确保时间戳为 ISO 格式字符串（兼容多种输入格式）。"""
    if not s:
        return datetime.now(UTC).isoformat()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.isoformat()
    except ValueError:
        return s


def import_session_data(db_path: str, session_dir: Path) -> str:
    """导入单组会话数据，返回 session_id。"""
    # 读取画像输入
    profile_file = session_dir / "00-输入画像与诊断切片.json"
    if not profile_file.exists():
        raise FileNotFoundError(f"缺少画像文件：{profile_file}")
    profile = json.loads(profile_file.read_text(encoding="utf-8"))

    # 读取各数据文件
    events_file = session_dir / "01-协同决策中间数据-事件流.jsonl"
    rounds_file = session_dir / "02-出题判分轮次明细.json"
    mastery_file = session_dir / "03-掌握度快照轨迹.json"
    packages_file = session_dir / "04-个性化资源包-三形态.json"

    events: list[dict] = []
    if events_file.exists():
        with events_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

    rounds: list[dict] = (
        json.loads(rounds_file.read_text(encoding="utf-8")) if rounds_file.exists() else []
    )
    mastery: list[dict] = (
        json.loads(mastery_file.read_text(encoding="utf-8")) if mastery_file.exists() else []
    )
    packages: list[dict] = (
        json.loads(packages_file.read_text(encoding="utf-8")) if packages_file.exists() else []
    )

    # session_id：以数据包内记录的 32 位 hash 为准（轮次/资源包行内含），
    # 目录名里只有 8 位短 hash，不能作为会话主键。
    session_id = profile.get("session_id")
    if not session_id:
        session_id = next(
            (r["session_id"] for r in rounds + packages if r.get("session_id")), ""
        )
    if len(session_id or "") < 16:
        raise ValueError(f"无法确定 session_id：{session_dir.name}")

    learner_id = profile.get("learner_id", "unknown")
    # diagnose_done 事件携带诊断摘要 → sessions.profile_summary
    profile_summary = next(
        (
            ev.get("payload", {}).get("summary", "")
            for ev in events
            if ev.get("event_type") == "diagnose_done"
        ),
        "",
    )

    # 推导时间基准
    first_created = rounds[0].get("created_at", "") if rounds else ""
    base_time = (
        datetime.fromisoformat(_parse_iso(first_created)) if first_created else datetime.now(UTC)
    )

    # 连接数据库并写入
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")

        # 幂等：先删旧数据（主题数/事件数/资源包数在查询时实时 COUNT，不落列）
        for table in (
            "session_events",
            "topic_rounds",
            "mastery_snapshots",
            "resource_packages",
            "exported_entries",
            "sessions",
        ):
            conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))

        # 学习者与画像（回放列表显示用）
        conn.execute(
            "INSERT INTO learners(id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (learner_id, learner_id),
        )
        inner_profile = profile.get("profile", {})
        conn.execute(
            """INSERT INTO learner_profiles(learner_id, background, mastery, style_tags)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(learner_id) DO UPDATE SET
                 background=excluded.background, mastery=excluded.mastery,
                 style_tags=excluded.style_tags, updated_at=datetime('now')""",
            (
                learner_id,
                json.dumps(inner_profile.get("background", {}), ensure_ascii=False),
                json.dumps(inner_profile.get("mastery", {}), ensure_ascii=False),
                json.dumps(inner_profile.get("style_tags", []), ensure_ascii=False),
            ),
        )

        # 写入 sessions
        conn.execute(
            "INSERT INTO sessions (session_id, learner_id, profile_json, gap_ids_json, "
            "difficulty_level, profile_summary, plan_json, status, created_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'finished', ?, ?)",
            (
                session_id,
                learner_id,
                json.dumps(inner_profile, ensure_ascii=False),
                json.dumps(profile.get("gap_ids", []), ensure_ascii=False),
                profile.get("difficulty_level"),
                profile_summary,
                json.dumps(profile.get("plan", {}), ensure_ascii=False),
                base_time.isoformat(),
                (
                    base_time
                    + timedelta(
                        seconds=max((ev.get("seq", 0) for ev in events), default=0)
                    )
                ).isoformat(),
            ),
        )

        # 写入 session_events（按 seq 秒展开时间戳）
        for ev in events:
            seq = ev.get("seq", 0)
            ev_time = (base_time + timedelta(seconds=seq)).isoformat()
            conn.execute(
                "INSERT INTO session_events (session_id, seq, event_type, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    seq,
                    ev.get("event_type", ""),
                    json.dumps(ev.get("payload", {}), ensure_ascii=False),
                    ev_time,
                ),
            )

        # 写入 topic_rounds
        for r in rounds:
            conn.execute(
                "INSERT INTO topic_rounds (session_id, entry_id, round_no, question_json, "
                "expected_json, answer_text, grade_json, decision, mastery_after, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    r.get("entry_id", ""),
                    r.get("round_no", 0),
                    json.dumps(r.get("question", {}), ensure_ascii=False),
                    json.dumps(r.get("expected", []), ensure_ascii=False),
                    r.get("answer"),
                    json.dumps(r.get("grade"), ensure_ascii=False) if r.get("grade") else None,
                    r.get("decision", ""),
                    r.get("mastery_after"),
                    r.get("created_at", base_time.isoformat()),
                ),
            )

        # 写入 mastery_snapshots
        for m in mastery:
            conn.execute(
                "INSERT INTO mastery_snapshots (session_id, learner_id, entry_id, round_no, "
                "correctness, mastery_after, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    profile.get("learner_id", "unknown"),
                    m.get("entry_id", ""),
                    m.get("round_no", 0),
                    int(bool(m.get("correctness", False))),
                    m.get("mastery_after", 0.0),
                    m.get("created_at", base_time.isoformat()),
                ),
            )

        # 写入 resource_packages
        for p in packages:
            conn.execute(
                "INSERT INTO resource_packages (session_id, learner_id, entry_id, lecture_json, "
                "questions_json, practice_json, challenge_json, difficulty_tier, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    p.get("learner_id", learner_id),
                    p.get("entry_id", ""),
                    json.dumps(p.get("lecture", []), ensure_ascii=False),
                    json.dumps(p.get("questions", []), ensure_ascii=False),
                    json.dumps(p.get("practice"), ensure_ascii=False) if p.get("practice") else None,
                    json.dumps(p.get("challenge"), ensure_ascii=False) if p.get("challenge") else None,
                    p.get("difficulty_tier", "beginner"),
                    base_time.isoformat(),
                ),
            )

        conn.commit()
    finally:
        conn.close()

    return session_id


def main() -> None:
    parser = argparse.ArgumentParser(description="导入测试数据包到数据库（无 LLM 回放演示用）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="数据库路径（默认 data/knowledge.db）")
    parser.add_argument("--data-dir", help="会话示例目录（默认自动查找交付包结构）")
    args = parser.parse_args()

    db_path = args.db
    data_dir = Path(args.data_dir) if args.data_dir else None

    # 自动查找测试数据目录
    if data_dir is None:
        # 尝试常见交付包位置
        candidates = [
            ROOT.parent / "03-测试数据包" / "02-会话示例",
            ROOT / "dist" / "交付包" / "03-测试数据包" / "02-会话示例",
            ROOT / "data" / "test-sessions",
        ]
        for c in candidates:
            if c.is_dir():
                data_dir = c
                break
        if data_dir is None:
            print("错误：未找到测试数据目录。请用 --data-dir 指定会话示例目录。")
            print("尝试过的位置：")
            for c in candidates:
                print(f"  {c}")
            return

    session_dirs = sorted([d for d in data_dir.iterdir() if d.is_dir()])
    if not session_dirs:
        print(f"错误：{data_dir} 下无会话目录")
        return

    print(f"导入 {len(session_dirs)} 组会话数据到 {db_path}")
    imported = 0
    for sd in session_dirs:
        try:
            sid = import_session_data(db_path, sd)
            imported += 1
            print(f"  [ok] {sd.name} -> {sid}")
        except Exception as e:
            print(f"  [fail] {sd.name}: {e}")

    print(f"\n完成：{imported}/{len(session_dirs)} 组导入成功")
    if imported > 0:
        print(f"启动后端后访问 http://localhost:8000 即可在会话列表看到 {imported} 组历史会话")


if __name__ == "__main__":
    main()
