"""
Badge Rules and Trigger Detection
=================================

A data-driven badge system. Each badge is defined by a ``BadgeRule``
containing a ``check_fn`` that receives a ``ProgressState`` and returns
True when the badge should be awarded.

Usage:
    from deeptutor.services.gamification.badges import check_all
    new_badges = check_all(progress_state)
    progress_state.badges.extend(new_badges)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from deeptutor.services.gamification.models import ProgressState


@dataclass
class BadgeRule:
    """Definition of a single badge.

    Attributes:
        badge_id: Unique stable identifier (e.g. ``"first_clear"``).
        name: Display name (child-friendly).
        description: Short description shown in the UI.
        icon: Emoji or icon name for the badge.
        check_fn: Pure function ``(ProgressState) -> bool`` that returns
            True when the badge's conditions are met.
    """

    badge_id: str
    name: str
    description: str
    icon: str
    check_fn: Callable[[ProgressState], bool]


# ---------------------------------------------------------------------------
# Individual badge check functions
# ---------------------------------------------------------------------------


def _check_first_clear(state: ProgressState) -> bool:
    """Awarded when the child clears at least one level (any map)."""
    for mp in state.maps.values():
        for level in mp.levels.values():
            if level.cleared:
                return True
    return False


def _check_combo_5(state: ProgressState) -> bool:
    """Awarded when the child achieves a combo of 5 or more."""
    return state.max_combo >= 5


def _check_streak_7(state: ProgressState) -> bool:
    """Awarded for a 7-day consecutive learning streak."""
    return state.streak_days >= 7


def _check_all_stars(state: ProgressState) -> bool:
    """Awarded when all levels in at least one map have 3 stars."""
    for mp in state.maps.values():
        if not mp.levels:
            continue
        if all(level.stars >= 3 for level in mp.levels.values()):
            return True
    return False


def _check_level_5(state: ProgressState) -> bool:
    """Awarded when the child reaches level 5."""
    return state.level >= 5


def _check_daily_goal(state: ProgressState) -> bool:
    """Awarded when the daily XP goal is met at least once."""
    return len(state.daily_goal_met_dates) >= 1


# ---------------------------------------------------------------------------
# Badge registry (data-driven)
# ---------------------------------------------------------------------------

BADGE_RULES: list[BadgeRule] = [
    BadgeRule(
        badge_id="first_clear",
        name="First Steps",
        description="Clear your very first level!",
        icon="🌟",
        check_fn=_check_first_clear,
    ),
    BadgeRule(
        badge_id="combo_5",
        name="On Fire!",
        description="Get 5 correct answers in a row.",
        icon="🔥",
        check_fn=_check_combo_5,
    ),
    BadgeRule(
        badge_id="streak_7",
        name="Week Warrior",
        description="Learn 7 days in a row.",
        icon="📅",
        check_fn=_check_streak_7,
    ),
    BadgeRule(
        badge_id="all_stars",
        name="Perfectionist",
        description="Get 3 stars on every level in a map.",
        icon="⭐",
        check_fn=_check_all_stars,
    ),
    BadgeRule(
        badge_id="level_5",
        name="Rising Star",
        description="Reach Level 5.",
        icon="🎖️",
        check_fn=_check_level_5,
    ),
    BadgeRule(
        badge_id="daily_goal",
        name="Daily Champion",
        description="Meet your daily XP goal.",
        icon="🎯",
        check_fn=_check_daily_goal,
    ),
]


def check_all(progress: ProgressState) -> list[str]:
    """
    Check all badge rules against the current progress state.

    Returns only the badge_ids that are newly triggered (i.e., not already
    in ``progress.badges``).

    Args:
        progress: The child's current gamification state.

    Returns:
        List of newly-earned badge_ids.
    """
    already_earned: set[str] = set(progress.badges)
    newly_earned: list[str] = []
    for rule in BADGE_RULES:
        if rule.badge_id in already_earned:
            continue
        if rule.check_fn(progress):
            newly_earned.append(rule.badge_id)
    return newly_earned


def get_badge_rule(badge_id: str) -> BadgeRule | None:
    """Look up a badge rule by ID. Returns None if not found."""
    for rule in BADGE_RULES:
        if rule.badge_id == badge_id:
            return rule
    return None


__all__ = [
    "BadgeRule",
    "BADGE_RULES",
    "check_all",
    "get_badge_rule",
]
