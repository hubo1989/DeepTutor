"""
Gamification Data Models
========================

Pure dataclasses for the gamification subsystem. No IO, no LLM, no
side effects — these are the canonical shapes that the engine, store,
badges, and API layers exchange.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LevelProgress:
    """Tracks a child's attempt state on a single level within a quest map."""

    level_id: str = ""
    stars: int = 0  # 0 = not attempted, 1-3 stars
    best_correct_pct: float = 0.0  # best percentage of correct answers
    attempts: int = 0  # number of times the level was attempted
    cleared: bool = False  # whether the child has passed this level (>=60%)
    last_played_at: str = ""  # ISO 8601 timestamp of last attempt


@dataclass
class MapProgress:
    """Tracks a child's progress on a single quest map (theme/topic)."""

    map_id: str = ""
    levels: dict[str, LevelProgress] = field(default_factory=dict)
    unlocked: bool = False
    completed: bool = False  # all levels cleared


@dataclass
class ProgressState:
    """
    The complete gamification state for one child profile.

    This is the root object persisted by ``store.py`` and consumed by
    ``engine.py`` and ``badges.py``.
    """

    profile_id: str = ""
    total_xp: int = 0
    level: int = 1  # computed from total_xp via compute_level
    daily_xp: int = 0  # XP earned today; reset daily
    daily_xp_date: str = ""  # ISO date for daily_xp tracking (YYYY-MM-DD)
    streak_days: int = 0
    streak_history: list[str] = field(default_factory=list)  # ISO dates
    maps: dict[str, MapProgress] = field(default_factory=dict)
    badges: list[str] = field(default_factory=list)  # badge_ids earned
    max_combo: int = 0  # best combo achieved across all sessions
    daily_goal_xp: int = 100  # XP target per day
    daily_goal_met_dates: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = 1


@dataclass
class Question:
    """A single question within a level.

    Supports eight question types used by the quest pipeline:
    ``single_choice``, ``image_choice``, ``true_false``, ``fill_blank``,
    ``matching``, ``ordering``, ``error_correction``, and ``boss_comprehensive``.

    Extra fields used by the forging / grading / hint subsystems:
        hints: Progressive hint texts (stage 1 structural hints).
        acceptable_answers: For ``fill_blank`` — all accepted variants
            (case-insensitive).  ``correct_answer`` is always the primary.
        correct_answer_ids: For ``error_correction`` — the set of option
            indices that identify the correct option.
        matching_pairs: For ``matching`` — list of ``[left, right]`` pairs.
        ordering_sequence: For ``ordering`` — the correct ordered list.
        sub_questions: For ``boss_comprehensive`` — nested question dicts.
    """

    question_id: str = ""
    text: str = ""
    question_type: str = "single_choice"
    options: list[str] = field(default_factory=list)
    correct_answer: str = ""
    explanation: str = ""
    points: int = 10  # base XP awarded for correct answer
    # Extended fields (T03)
    hints: list[str] = field(default_factory=list)
    acceptable_answers: list[str] = field(default_factory=list)
    correct_answer_ids: list[str] = field(default_factory=list)
    matching_pairs: list[list[str]] = field(default_factory=list)
    ordering_sequence: list[str] = field(default_factory=list)
    sub_questions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Level:
    """A level within a quest map."""

    level_id: str = ""
    title: str = ""
    description: str = ""
    order: int = 0  # position in the map (0-based)
    is_boss: bool = False  # boss levels gate further progression
    questions: list[Question] = field(default_factory=list)
    unlock_dependency: str = ""  # level_id that must be cleared first


@dataclass
class QuestMap:
    """A themed collection of levels forming a learning path."""

    map_id: str = ""
    title: str = ""
    description: str = ""
    theme: str = ""  # e.g. "math", "science", "language"
    icon: str = ""
    order: int = 0
    levels: list[Level] = field(default_factory=list)
    unlock_dependency: str = ""  # map_id that must be completed first


__all__ = [
    "LevelProgress",
    "MapProgress",
    "ProgressState",
    "Question",
    "Level",
    "QuestMap",
]
