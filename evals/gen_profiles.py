"""生成 50 组评测画像种子（W2.2）：3 组手工精设 + 47 组程序生成。

用法：uv run python evals/gen_profiles.py
输出：evals/profiles/ 下 50 个 JSON（p01 ~ p50，前 3 组为手工精设演示画像）。

设计：
- 画像维度正交采样：教育背景 × 专业 × 目标 × 经验组合 × 风格标签——
  覆盖 beginner/intermediate/advanced 三档，让诊断与难度适配有区分度；
- 程序生成用固定随机种子（可复现）；
- 经验描述直接驱动 difficulty_level 诊断（零基础 → 有工具经验 → 有项目经验）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT_DIR = Path(__file__).parent / "profiles"

# 三组手工精设（演示用，覆盖三个难度档 + 两种学习动机）
HANDCRAFTED: list[dict] = [
    {
        "learner_id": "p01",
        "name": "零基础转行者·文科背景",
        "background": {
            "education": "本科毕业 3 年",
            "major": "汉语言文学",
            "goal": "转行数据运营",
            "experience": "仅会 Office 基础操作，未接触过数据库、SQL 和任何编程语言",
        },
        "mastery": {},
        "style_tags": ["analogy-first", "step-by-step"],
        "note": "手工精设：绝对零基础——检验 beginner 难度闸门与生活类比教学",
    },
    {
        "learner_id": "p02",
        "name": "工科在读·有编程基础",
        "background": {
            "education": "本科大三",
            "major": "机械工程",
            "goal": "拿到数据分析实习",
            "experience": "学过 C 语言和数据结构，会用 Excel 做透视表，未接触过数据库和 Python",
        },
        "mastery": {},
        "style_tags": ["code-first"],
        "note": "手工精设：有编程迁移能力——检验 intermediate 判定与代码优先风格",
    },
    {
        "learner_id": "p03",
        "name": "在职补短板·有工具经验",
        "background": {
            "education": "硕士毕业",
            "major": "统计学",
            "goal": "补齐工程化短板，转向数据工程",
            "experience": "熟悉统计建模与 R 语言，用过 MySQL 做简单查询，Python 仅会调包",
        },
        "mastery": {},
        "style_tags": ["code-first", "concise"],
        "note": "手工精设：统计强工程弱——检验 advanced 判定与查漏式切片",
    },
]

EDUCATIONS = [
    "本科大二", "本科大三", "本科大四", "本科毕业 1 年", "本科毕业 3 年",
    "专科毕业 2 年", "硕士在读", "硕士毕业",
]
MAJORS = [
    "机械工程", "电气工程", "土木工程", "电子信息", "计算机科学",
    "软件工程", "市场营销", "工商管理学", "金融学", "会计学",
    "生物科学", "化学", "汉语言文学", "英语", "新闻传播学",
    "工业设计", "物流管理", "人力资源管理", "护理学",
]
GOALS = [
    "转行数据分析", "转行数据运营", "拿到数据分析实习", "提升职场竞争力",
    "为考研大数据方向做准备", "胜任现有岗位的数据工作", "转行商业分析",
    "搭建业务数据看板",
]
# 经验 × 对应预期难度档（驱动诊断 difficulty_level 的主要信号）
EXPERIENCES: list[tuple[str, str]] = [
    ("未接触过数据库、SQL 和任何编程语言，仅会 Office 基础操作", "beginner"),
    ("学过 C 语言基础，会用 Excel 做简单统计，未接触过数据库和 Python", "beginner"),
    ("会写简单 Python 脚本处理 Excel，未系统学过数据库", "intermediate"),
    ("用过 SQL 做增删改查，Python 会 pandas 基础操作", "intermediate"),
    ("学过数据库原理课程，会多表 JOIN 查询，Python 仅会调包", "intermediate"),
    ("工作中有 SQL 取数经验，熟悉 pandas，想系统补统计基础", "advanced"),
    ("熟悉统计建模与 R 语言，用过 MySQL，Python 会写自动化脚本", "advanced"),
    ("数据岗在职，日常用 SQL + Python，想补机器学习前的数据工程基础", "advanced"),
]
STYLE_TAGS = [
    ["code-first"], ["analogy-first"], ["step-by-step"],
    ["code-first", "concise"], ["analogy-first", "step-by-step"],
    ["concise"],
]


def generate() -> list[dict]:
    rng = random.Random(42)  # 固定种子：画像集可复现
    profiles: list[dict] = list(HANDCRAFTED)
    seen_names = {p["name"] for p in profiles}
    idx = 4
    while len(profiles) < 50:
        edu = rng.choice(EDUCATIONS)
        major = rng.choice(MAJORS)
        goal = rng.choice(GOALS)
        exp, expect_level = rng.choice(EXPERIENCES)
        tags = rng.choice(STYLE_TAGS)
        name = f"{edu}·{major}"
        if name in seen_names:
            name = f"{name}（{idx:02d}）"
        seen_names.add(name)
        profiles.append(
            {
                "learner_id": f"p{idx:02d}",
                "name": name,
                "background": {
                    "education": edu,
                    "major": major,
                    "goal": goal,
                    "experience": exp,
                },
                "mastery": {},
                "style_tags": tags,
                "expect_level": expect_level,  # 评测侧对照用（不进诊断上下文）
            }
        )
        idx += 1
    return profiles


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUT_DIR.glob("p*.json"):
        f.unlink()
    profiles = generate()
    for p in profiles:
        (OUT_DIR / f"{p['learner_id']}.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    levels = [p.get("expect_level", "手工") for p in profiles]
    print(f"生成 {len(profiles)} 组 → {OUT_DIR}")
    print("分布:", {lv: levels.count(lv) for lv in sorted(set(levels))})


if __name__ == "__main__":
    main()
