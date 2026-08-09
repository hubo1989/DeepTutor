"""
Tests for the gamification engine — pure function coverage.

Covers: XP computation (base, combo, boss, replay, daily cap),
star grading, unlock logic, streak computation, and level mapping.
"""

from __future__ import annotations

import pytest

from deeptutor.services.gamification.engine import (
    BOSS_MULTIPLIER,
    COMBO_MULTIPLIER,
    DEFAULT_DAILY_XP_CAP,
    LEVEL_THRESHOLDS,
    MAX_LEVEL,
    REPLAY_FACTOR,
    apply_diminishing_returns,
    apply_xp_cap,
    award_xp,
    base_xp_for_correct,
    check_unlock,
    compute_boss_bonus,
    compute_combo_bonus,
    compute_level,
    grade_stars,
    update_streak,
    xp_for_next_level,
)

# ---------------------------------------------------------------------------
# Base XP
# ---------------------------------------------------------------------------


class TestBaseXP:
    def test_zero_correct(self):
        assert base_xp_for_correct(0) == 0

    def test_one_correct(self):
        assert base_xp_for_correct(1) == 10

    def test_multiple_correct(self):
        assert base_xp_for_correct(5) == 50

    def test_negative_correct(self):
        assert base_xp_for_correct(-3) == 0


# ---------------------------------------------------------------------------
# Diminishing returns (replay)
# ---------------------------------------------------------------------------


class TestDiminishingReturns:
    def test_default_factor_halves(self):
        assert apply_diminishing_returns(100) == 50

    def test_custom_factor(self):
        assert apply_diminishing_returns(100, factor=0.25) == 25

    def test_zero_xp(self):
        assert apply_diminishing_returns(0) == 0

    def test_negative_xp(self):
        assert apply_diminishing_returns(-50) == 0


# ---------------------------------------------------------------------------
# XP cap
# ---------------------------------------------------------------------------


class TestXPCap:
    def test_under_cap(self):
        assert apply_xp_cap(100, cap=200) == 100

    def test_at_cap(self):
        assert apply_xp_cap(200, cap=200) == 200

    def test_over_cap(self):
        assert apply_xp_cap(300, cap=200) == 200

    def test_zero_cap(self):
        assert apply_xp_cap(100, cap=0) == 0


# ---------------------------------------------------------------------------
# Combo bonus
# ---------------------------------------------------------------------------


class TestComboBonus:
    def test_no_combo(self):
        assert compute_combo_bonus(100, combo=0) == 100

    def test_combo_below_threshold(self):
        assert compute_combo_bonus(100, combo=2) == 100

    def test_combo_at_threshold(self):
        assert compute_combo_bonus(100, combo=3) == 150

    def test_combo_above_threshold(self):
        assert compute_combo_bonus(100, combo=5) == 150


# ---------------------------------------------------------------------------
# Boss bonus
# ---------------------------------------------------------------------------


class TestBossBonus:
    def test_normal_level(self):
        assert compute_boss_bonus(100, is_boss=False) == 100

    def test_boss_level(self):
        assert compute_boss_bonus(100, is_boss=True) == 200


# ---------------------------------------------------------------------------
# Full award_xp
# ---------------------------------------------------------------------------


class TestAwardXP:
    def test_zero_correct_returns_zero(self):
        assert award_xp(current_xp=0, correct_count=0) == 0

    def test_negative_correct_returns_zero(self):
        assert award_xp(current_xp=100, correct_count=-5) == 0

    def test_base_xp_no_multipliers(self):
        # 3 correct × 10 = 30 XP, no combo, no boss, no replay
        result = award_xp(current_xp=0, correct_count=3, combo=0, is_boss=False)
        assert result == 30

    def test_boss_double_xp(self):
        # 3 correct × 10 × 2.0 = 60 XP
        result = award_xp(current_xp=0, correct_count=3, combo=0, is_boss=True)
        assert result == 60

    def test_combo_multiplier_at_combo_3(self):
        # 3 correct × 10 × 1.5 = 45 XP
        result = award_xp(current_xp=0, correct_count=3, combo=3, is_boss=False)
        assert result == 45

    def test_combo_multiplier_at_combo_5(self):
        # 3 correct × 10 × 1.5 = 45 XP (same multiplier)
        result = award_xp(current_xp=0, correct_count=3, combo=5, is_boss=False)
        assert result == 45

    def test_boss_and_combo_combined(self):
        # 3 correct × 10 × 2.0 × 1.5 = 90 XP
        result = award_xp(current_xp=0, correct_count=3, combo=3, is_boss=True)
        assert result == 90

    def test_replay_diminishing(self):
        # 3 correct × 10 × 0.5 = 15 XP
        result = award_xp(current_xp=0, correct_count=3, combo=0, is_boss=False, is_replay=True)
        assert result == 15

    def test_replay_with_combo_and_boss(self):
        # 3 correct × 10 × 2.0 × 1.5 × 0.5 = 45 XP
        result = award_xp(current_xp=0, correct_count=3, combo=3, is_boss=True, is_replay=True)
        assert result == 45

    def test_daily_cap_truncates_xp(self):
        # Already 190/200 today, earning 30 → only 10 remains
        result = award_xp(
            current_xp=100,
            correct_count=3,
            daily_xp_today=190,
            daily_cap=200,
        )
        assert result == 10

    def test_daily_cap_zero_headroom(self):
        result = award_xp(
            current_xp=100,
            correct_count=3,
            daily_xp_today=200,
            daily_cap=200,
        )
        assert result == 0

    def test_daily_cap_negative_headroom(self):
        result = award_xp(
            current_xp=100,
            correct_count=3,
            daily_xp_today=250,
            daily_cap=200,
        )
        assert result == 0

    def test_full_load(self):
        # Boss level, combo 5, replay, near daily cap
        # 3 correct × 10 × 2.0 × 1.5 × 0.5 = 45 XP, but only 20 headroom
        result = award_xp(
            current_xp=500,
            correct_count=3,
            combo=5,
            is_boss=True,
            is_replay=True,
            daily_xp_today=180,
            daily_cap=200,
        )
        assert result == 20


# ---------------------------------------------------------------------------
# Star grading
# ---------------------------------------------------------------------------


class TestGradeStars:
    def test_zero_pct(self):
        assert grade_stars(0.0) == 0

    def test_below_60_pct(self):
        assert grade_stars(0.59) == 0

    def test_exactly_60_pct(self):
        assert grade_stars(0.60) == 1

    def test_70_pct(self):
        assert grade_stars(0.70) == 1

    def test_exactly_80_pct(self):
        assert grade_stars(0.80) == 2

    def test_90_pct(self):
        assert grade_stars(0.90) == 2

    def test_exactly_100_pct(self):
        assert grade_stars(1.00) == 3

    def test_above_100_pct(self):
        assert grade_stars(1.5) == 3


# ---------------------------------------------------------------------------
# Unlock logic
# ---------------------------------------------------------------------------


class TestCheckUnlock:
    def test_first_level_normal(self):
        # No previous level result (treat as always unlocked)
        assert check_unlock(prev_level_result=1.0, is_boss_level=False, all_prev_stars=[]) is True

    def test_normal_unlock_at_threshold(self):
        assert check_unlock(prev_level_result=0.60, is_boss_level=False, all_prev_stars=[]) is True

    def test_normal_unlock_below_threshold(self):
        assert check_unlock(prev_level_result=0.59, is_boss_level=False, all_prev_stars=[]) is False

    def test_boss_unlock_all_prev_starred(self):
        assert (
            check_unlock(prev_level_result=0.90, is_boss_level=True, all_prev_stars=[1, 2, 3])
            is True
        )

    def test_boss_unlock_missing_star(self):
        assert (
            check_unlock(prev_level_result=0.90, is_boss_level=True, all_prev_stars=[1, 0, 3])
            is False
        )

    def test_boss_unlock_all_zero(self):
        assert (
            check_unlock(prev_level_result=0.90, is_boss_level=True, all_prev_stars=[0, 0]) is False
        )

    def test_boss_unlock_empty_prev(self):
        assert check_unlock(prev_level_result=1.0, is_boss_level=True, all_prev_stars=[]) is True

    def test_high_result_non_boss(self):
        assert check_unlock(prev_level_result=1.0, is_boss_level=False, all_prev_stars=[]) is True


# ---------------------------------------------------------------------------
# Streak computation
# ---------------------------------------------------------------------------


class TestUpdateStreak:
    def test_empty_history_first_day(self):
        days, hist = update_streak([], "2024-01-15")
        assert days == 1
        assert hist == ["2024-01-15"]

    def test_consecutive_day(self):
        hist = ["2024-01-14", "2024-01-15"]
        days, new_hist = update_streak(hist, "2024-01-16")
        assert days == 3
        assert "2024-01-16" in new_hist

    def test_gap_resets_streak(self):
        hist = ["2024-01-10", "2024-01-11", "2024-01-12"]
        days, new_hist = update_streak(hist, "2024-01-15")
        assert days == 1
        assert new_hist == ["2024-01-15"]

    def test_same_day_idempotent(self):
        hist = ["2024-01-15"]
        days, new_hist = update_streak(hist, "2024-01-15")
        assert days == 1
        assert new_hist == ["2024-01-15"]

    def test_two_day_gap(self):
        hist = ["2024-01-01"]
        days, new_hist = update_streak(hist, "2024-01-04")
        assert days == 1
        assert new_hist == ["2024-01-04"]

    def test_invalid_date_returns_unchanged(self):
        hist = ["2024-01-15"]
        days, new_hist = update_streak(hist, "invalid-date")
        assert days == 1
        assert new_hist == ["2024-01-15"]

    def test_full_datetime_string(self):
        hist = ["2024-01-15"]
        days, new_hist = update_streak(hist, "2024-01-16T10:30:00")
        assert days == 2
        assert "2024-01-16T10:30:00" in new_hist


# ---------------------------------------------------------------------------
# Level computation
# ---------------------------------------------------------------------------


class TestComputeLevel:
    def test_zero_xp(self):
        assert compute_level(0) == 1

    def test_negative_xp(self):
        assert compute_level(-10) == 1

    def test_level_2(self):
        assert compute_level(50) == 2

    def test_just_below_level_3(self):
        assert compute_level(119) == 2

    def test_level_3(self):
        assert compute_level(120) == 3

    def test_level_5(self):
        assert compute_level(350) == 5

    def test_level_10(self):
        assert compute_level(1680) == 10

    def test_above_max_level(self):
        assert compute_level(10000) == 10

    def test_all_thresholds(self):
        for i, threshold in enumerate(LEVEL_THRESHOLDS):
            level = compute_level(threshold)
            assert level == min(i + 1, MAX_LEVEL)

    def test_xp_for_next_level(self):
        assert xp_for_next_level(1) == 50
        assert xp_for_next_level(5) == 520
        assert xp_for_next_level(10) == LEVEL_THRESHOLDS[-1]


# ---------------------------------------------------------------------------
# Combo interruption behavior
# ---------------------------------------------------------------------------


class TestComboInterruption:
    def test_combo_reset_on_wrong_answer(self):
        """When combo is broken, the next award should not have the multiplier."""
        # Combo was 5 but now broken → combo=0
        result_with = award_xp(current_xp=0, correct_count=1, combo=5)
        result_without = award_xp(current_xp=0, correct_count=1, combo=0)
        assert result_with == 15  # with 1.5× multiplier
        assert result_without == 10  # without

    def test_downgrade_replay_halves(self):
        """Replaying a cleared level should yield half the XP."""
        full = award_xp(current_xp=0, correct_count=2, is_replay=False)
        half = award_xp(current_xp=0, correct_count=2, is_replay=True)
        assert full == 20
        assert half == 10
