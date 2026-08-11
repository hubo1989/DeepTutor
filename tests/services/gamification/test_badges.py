"""
Tests for badge trigger conditions.
"""

from __future__ import annotations

import pytest

from deeptutor.services.gamification.badges import BADGE_RULES, check_all, get_badge_rule
from deeptutor.services.gamification.models import LevelProgress, MapProgress, ProgressState


def _make_empty_state() -> ProgressState:
    """Create a ProgressState with no progress for baseline tests."""
    return ProgressState(profile_id="test-kid")


def _make_state_with_cleared_level() -> ProgressState:
    """Create a state with one cleared level."""
    state = _make_empty_state()
    mp = MapProgress(map_id="map1")
    mp.levels["lvl1"] = LevelProgress(level_id="lvl1", cleared=True, stars=2)
    state.maps["map1"] = mp
    return state


def _make_state_with_combo(combo: int) -> ProgressState:
    state = _make_empty_state()
    state.max_combo = combo
    return state


def _make_state_with_streak(days: int) -> ProgressState:
    state = _make_empty_state()
    state.streak_days = days
    return state


def _make_state_with_all_stars() -> ProgressState:
    state = _make_empty_state()
    mp = MapProgress(map_id="map1")
    mp.levels["lvl1"] = LevelProgress(level_id="lvl1", stars=3)
    mp.levels["lvl2"] = LevelProgress(level_id="lvl2", stars=3)
    state.maps["map1"] = mp
    return state


def _make_state_with_level(lvl: int) -> ProgressState:
    state = _make_empty_state()
    state.level = lvl
    return state


def _make_state_with_daily_goal() -> ProgressState:
    state = _make_empty_state()
    state.daily_goal_met_dates = ["2024-01-15"]
    return state


# ---------------------------------------------------------------------------
# Individual badge tests
# ---------------------------------------------------------------------------


class TestFirstClear:
    def test_no_levels_cleared(self):
        state = _make_empty_state()
        assert "first_clear" not in check_all(state)

    def test_one_level_cleared(self):
        state = _make_state_with_cleared_level()
        assert "first_clear" in check_all(state)

    def test_not_re_awarded(self):
        state = _make_state_with_cleared_level()
        state.badges.append("first_clear")
        assert "first_clear" not in check_all(state)


class TestCombo5:
    def test_combo_below_5(self):
        state = _make_state_with_combo(4)
        assert "combo_5" not in check_all(state)

    def test_combo_exactly_5(self):
        state = _make_state_with_combo(5)
        assert "combo_5" in check_all(state)

    def test_combo_above_5(self):
        state = _make_state_with_combo(10)
        assert "combo_5" in check_all(state)


class TestStreak7:
    def test_streak_below_7(self):
        state = _make_state_with_streak(6)
        assert "streak_7" not in check_all(state)

    def test_streak_exactly_7(self):
        state = _make_state_with_streak(7)
        assert "streak_7" in check_all(state)

    def test_streak_above_7(self):
        state = _make_state_with_streak(14)
        assert "streak_7" in check_all(state)


class TestAllStars:
    def test_not_all_three_stars(self):
        state = _make_state_with_cleared_level()  # only 2 stars
        assert "all_stars" not in check_all(state)

    def test_all_three_stars(self):
        state = _make_state_with_all_stars()
        assert "all_stars" in check_all(state)

    def test_empty_map_no_badge(self):
        state = _make_empty_state()
        state.maps["map1"] = MapProgress(map_id="map1")
        assert "all_stars" not in check_all(state)


class TestLevel5:
    def test_below_level_5(self):
        state = _make_state_with_level(4)
        assert "level_5" not in check_all(state)

    def test_exactly_level_5(self):
        state = _make_state_with_level(5)
        assert "level_5" in check_all(state)

    def test_above_level_5(self):
        state = _make_state_with_level(8)
        assert "level_5" in check_all(state)


class TestDailyGoal:
    def test_no_goal_met(self):
        state = _make_empty_state()
        assert "daily_goal" not in check_all(state)

    def test_goal_met_once(self):
        state = _make_state_with_daily_goal()
        assert "daily_goal" in check_all(state)


# ---------------------------------------------------------------------------
# Combined badge tests
# ---------------------------------------------------------------------------


class TestCheckAll:
    def test_empty_state_no_badges(self):
        state = _make_empty_state()
        assert check_all(state) == []

    def test_multiple_badges_at_once(self):
        state = _make_state_with_cleared_level()
        state.max_combo = 7
        state.streak_days = 10
        state.level = 6
        state.daily_goal_met_dates = ["2024-01-15"]
        result = check_all(state)
        # Should earn: first_clear, combo_5, streak_7, level_5, daily_goal
        assert "first_clear" in result
        assert "combo_5" in result
        assert "streak_7" in result
        assert "level_5" in result
        assert "daily_goal" in result
        # all_stars not earned (only 2 stars)
        assert "all_stars" not in result

    def test_already_earned_not_re_triggered(self):
        state = _make_state_with_cleared_level()
        # Pre-award all badges
        all_ids = [r.badge_id for r in BADGE_RULES]
        state.badges = list(all_ids)
        assert check_all(state) == []


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestBadgeRegistry:
    def test_all_six_badges_defined(self):
        assert len(BADGE_RULES) == 6

    def test_unique_ids(self):
        ids = [r.badge_id for r in BADGE_RULES]
        assert len(ids) == len(set(ids))

    def test_get_badge_rule_found(self):
        rule = get_badge_rule("first_clear")
        assert rule is not None
        assert rule.name == "First Steps"

    def test_get_badge_rule_not_found(self):
        assert get_badge_rule("nonexistent") is None
