"""掌握度数学：加权、置信度封顶、门槛、进/停/退决策。"""

from core.mastery import (
    MASTERY_GATE,
    compute_mastery,
    decide_next_step,
    is_mastered,
)


def test_empty_history_zero():
    assert compute_mastery([]) == 0.0


def test_all_correct_mastered():
    m = compute_mastery([True, True, True])
    assert m == 1.0
    assert is_mastered(m)


def test_all_wrong_zero():
    assert compute_mastery([False, False, False]) == 0.0


def test_single_correct_capped_by_confidence():
    m = compute_mastery([True])
    assert m == 0.5  # 单次答对不能宣告掌握


def test_two_correct_still_capped():
    m = compute_mastery([True, True])
    assert m == 0.8  # 二次答对仍封顶


def test_three_correct_no_cap():
    assert compute_mastery([True, True, True]) == 1.0


def test_recency_rewards_recovery():
    """早期全错、最近答对：新作答权重高，掌握度应明显高于 0。"""
    m = compute_mastery([False, False, True])
    assert 0.0 < m < 1.0
    assert m > 0.3


def test_long_history_uses_last_five():
    m = compute_mastery([False] * 10 + [True] * 5)
    assert m == 1.0  # 最近 5 次全对即满分


def test_decide_retry_below_gate():
    decision, mastery = decide_next_step([False])
    assert decision == "retry"
    assert mastery == 0.0


def test_decide_advance_after_recovery():
    decision, _ = decide_next_step([False, True, True, True])
    assert decision == "advance"


def test_decide_regress_after_two_consecutive_wrong():
    decision, _ = decide_next_step([False, False])
    assert decision == "regress"


def test_decide_regress_wins_over_gate():
    """连续答错触发降维优先于其他判定（地基没打牢）。"""
    decision, _ = decide_next_step([True, False, False])
    assert decision == "regress"


def test_decide_advance_after_max_attempts():
    """轮次超限但未达门槛：防死循环放行（partial 由 mastery 差距暴露）。"""
    decision, mastery = decide_next_step([False, False, True, True, False], max_attempts=5)
    assert decision == "advance"
    assert mastery < MASTERY_GATE
