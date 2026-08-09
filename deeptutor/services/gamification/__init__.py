"""
Gamification Service — module exports.

This package contains the pure-function game engine (XP, stars, unlocks,
streaks), badge definitions, and JSON persistence for child learning profiles.
"""

from deeptutor.services.gamification.badges import BADGE_RULES, check_all
from deeptutor.services.gamification.engine import (
    apply_diminishing_returns,
    apply_xp_cap,
    award_xp,
    check_unlock,
    compute_level,
    grade_stars,
    update_streak,
)
from deeptutor.services.gamification.models import (
    Level,
    LevelProgress,
    MapProgress,
    ProgressState,
    Question,
    QuestMap,
)
from deeptutor.services.gamification.store import init_if_absent, load, save

__all__ = [
    # Models
    "Level",
    "LevelProgress",
    "MapProgress",
    "ProgressState",
    "Question",
    "QuestMap",
    # Engine
    "award_xp",
    "grade_stars",
    "check_unlock",
    "update_streak",
    "compute_level",
    "apply_xp_cap",
    "apply_diminishing_returns",
    # Badges
    "BADGE_RULES",
    "check_all",
    # Store
    "init_if_absent",
    "load",
    "save",
]
