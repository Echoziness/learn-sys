"""掌握度数学——全链路唯一的教学数值事实源。纯函数，无 I/O、无 LLM。

设计（2026-07-22 对齐 DeepTutor learning/mastery.py 的认知，适配本系统）：

- recency-weighted：最近 5 次作答按新旧加权，答错后恢复会被奖励；
- 置信度封顶：作答次数不足时掌握度有硬上限——单次/两次答对绝不宣告掌握，
  防止"碰巧答对即通关"；
- 门槛判定与下一步决策分离：is_mastered 是"能否前进"的硬裁决，
  decide_next_step 是"进/停/退"的教学决策——裁决归属引擎，措辞归属模型。
"""

from __future__ import annotations

from typing import Literal

# 最近作答的权重（旧 → 新）。新作答权重更高，早期失误后的恢复会被加权补偿。
_RECENCY_WEIGHTS: tuple[float, ...] = (0.5, 0.7, 0.85, 0.95, 1.0)

# 作答次数不足时的掌握度硬上限：{已作答次数: 上限}。第 3 次起不再封顶。
_CONFIDENCE_CAP: dict[int, float] = {1: 0.5, 2: 0.8}

# 掌握门槛：mastery >= MASTERY_GATE 才算"已掌握"。
# 演示取 0.7（DeepTutor 长期学习用 0.9；单次演示需要能看见进步）。
MASTERY_GATE: float = 0.7

# 连续答错多少次触发降维（回到前置主题重新教）。连错是"地基没打牢"的最强信号。
REGRESS_AFTER_CONSECUTIVE_WRONG: int = 2

# 单主题内问答轮次上限，超过则按"部分掌握"放行并标注（防死循环）。
MAX_ATTEMPTS_PER_TOPIC: int = 5


def compute_mastery(correctness: list[bool]) -> float:
    """由作答历史计算 0.0-1.0 掌握度（时间正序传入）。

    - 空历史返回 0.0；
    - 按最近至多 5 次加权平均；
    - 证据不足时受置信度上限约束。
    """
    if not correctness:
        return 0.0
    recent = correctness[-len(_RECENCY_WEIGHTS):]
    weights = _RECENCY_WEIGHTS[-len(recent):]
    score = sum(w * (1.0 if c else 0.0) for c, w in zip(recent, weights, strict=True)) / sum(
        weights
    )
    return min(score, _CONFIDENCE_CAP.get(len(recent), 1.0))


def is_mastered(mastery: float, gate: float = MASTERY_GATE) -> bool:
    """硬门槛裁决：掌握度达到门槛才算已掌握。门槛是前进的唯一依据。"""
    return mastery >= gate


NextStep = Literal["advance", "retry", "regress"]


def decide_next_step(
    correctness: list[bool],
    *,
    gate: float = MASTERY_GATE,
    regress_after: int = REGRESS_AFTER_CONSECUTIVE_WRONG,
    max_attempts: int = MAX_ATTEMPTS_PER_TOPIC,
) -> tuple[NextStep, float]:
    """教学决策：进 / 停（重教） / 退（降维）。

    优先级（对齐 DeepTutor policy.next_objective 的认知）：
    1. 连续答错达到阈值 → regress（地基没打牢，回前置主题）；
    2. 达到门槛 → advance（已掌握，可前进）；
    3. 轮次超限 → advance 但标注 partial（防死循环，放行给收尾流程判断）；
    4. 否则 → retry（留在本主题继续教）。

    返回 (decision, mastery)。partial 放行通过 mastery 值与 gate 的差距暴露。
    """
    mastery = compute_mastery(correctness)
    if len(correctness) >= regress_after and not correctness[-regress_after:].count(True):
        return "regress", mastery
    if is_mastered(mastery, gate):
        return "advance", mastery
    if len(correctness) >= max_attempts:
        return "advance", mastery
    return "retry", mastery


__all__ = [
    "MASTERY_GATE",
    "REGRESS_AFTER_CONSECUTIVE_WRONG",
    "MAX_ATTEMPTS_PER_TOPIC",
    "NextStep",
    "compute_mastery",
    "is_mastered",
    "decide_next_step",
]
