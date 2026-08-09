"""
Gamification Engine — Pure Functions
=====================================

Every function in this module is a **pure function**: no IO, no LLM calls,
no network access, no side effects. Given the same inputs, the output is
always identical. This determinism is what guarantees fairness — the
"judgment axioms" of the gamification system.

Conventions:
  - ``correct_count`` / ``total_questions`` are raw integers.
  - ``combo`` is the current consecutive-correct streak.
  - All XP values are non-negative integers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from deeptutor.services.gamification.models import ProgressState, QuestMap

# ---------------------------------------------------------------------------
# XP thresholds for level computation.
#   compute_level(total_xp) scans this list; the index of the first threshold
#   that exceeds total_xp determines the level (index + 1).
#   Level 10 is the cap: total_xp >= 1680 → Lv.10.
# ---------------------------------------------------------------------------
LEVEL_THRESHOLDS: list[int] = [0, 50, 120, 220, 350, 520, 730, 990, 1300, 1680]
MAX_LEVEL: int = 10

# Daily XP cap — prevents marathon sessions from over-leveling.
DEFAULT_DAILY_XP_CAP: int = 200

# Multipliers
COMBO_MULTIPLIER: float = 1.5  # applied at combo milestones
BOSS_MULTIPLIER: float = 2.0   # boss levels double the XP
REPLAY_FACTOR: float = 0.5     # already-cleared levels yield half XP

# Star thresholds (percentage of correct answers)
STAR_1_THRESHOLD: float = 0.60
STAR_2_THRESHOLD: float = 0.80
STAR_3_THRESHOLD: float = 1.00

# Unlock threshold — the minimum correctness on the previous level.
UNLOCK_CORRECT_THRESHOLD: float = 0.60


# ---------------------------------------------------------------------------
# Core XP computation
# ---------------------------------------------------------------------------

def base_xp_for_correct(correct_count: int) -> int:
    """Compute raw XP from correct answers (10 XP per correct answer).

    Args:
        correct_count: Number of correct answers.

    Returns:
        Raw base XP before multipliers.
    """
    return max(0, correct_count) * 10


def apply_diminishing_returns(xp: int, factor: float = REPLAY_FACTOR) -> int:
    """Reduce XP earned from re-playing an already-cleared level.

    Args:
        xp: The XP that would normally be awarded.
        factor: Diminishing factor (default 0.5 = halved).

    Returns:
        The reduced XP value, floored to an integer.
    """
    return int(max(0, xp) * factor)


def apply_xp_cap(daily_xp: int, cap: int = DEFAULT_DAILY_XP_CAP) -> int:
    """Cap the total daily XP to prevent over-leveling.

    Args:
        daily_xp: XP earned so far today.
        cap: Maximum daily XP allowed.

    Returns:
        The capped daily XP (never exceeds ``cap``).
    """
    return min(daily_xp, cap)


def compute_combo_bonus(xp: int, combo: int) -> int:
    """Apply the combo multiplier when combo reaches threshold.

    The 1.5× multiplier kicks in at combo ≥ 3 and applies to the full
    XP for that answer batch.

    Args:
        xp: Base XP for this batch of correct answers.
        combo: Current consecutive-correct streak.

    Returns:
        XP with combo multiplier applied if eligible.
    """
    if combo >= 3:
        return int(xp * COMBO_MULTIPLIER)
    return xp


def compute_boss_bonus(xp: int, is_boss: bool) -> int:
    """Double XP for boss levels.

    Args:
        xp: Base XP.
        is_boss: Whether the level is a boss level.

    Returns:
        XP with boss multiplier applied if applicable.
    """
    if is_boss:
        return int(xp * BOSS_MULTIPLIER)
    return xp


def award_xp(
    current_xp: int,
    correct_count: int,
    combo: int = 0,
    is_boss: bool = False,
    *,
    is_replay: bool = False,
    daily_xp_today: int = 0,
    daily_cap: int = DEFAULT_DAILY_XP_CAP,
) -> int:
    """
    Compute the **new XP** to add, applying all multipliers and caps.

    Order of operations:
      1. Base XP from correct_count (10 XP each)
      2. Boss multiplier (×2.0)
      3. Combo multiplier (×1.5 at combo ≥ 3)
      4. Replay diminishing returns (×0.5 if already cleared)
      5. Daily cap enforcement (remaining headroom only)

    Args:
        current_xp: The child's total XP *before* this award (used for
            context; the return value is the delta to add).
        correct_count: Correct answers in this attempt.
        combo: Current consecutive-correct streak.
        is_boss: Whether the completed level is a boss level.
        is_replay: Whether the level was already cleared (diminishing returns).
        daily_xp_today: XP already earned today (for cap enforcement).
        daily_cap: Maximum XP per day.

    Returns:
        The XP delta to add to the child's total (may be 0 if daily cap hit).
    """
    if correct_count <= 0:
        return 0

    xp = base_xp_for_correct(correct_count)

    # Boss multiplier
    xp = compute_boss_bonus(xp, is_boss)

    # Combo multiplier
    xp = compute_combo_bonus(xp, combo)

    # Replay diminishing returns
    if is_replay:
        xp = apply_diminishing_returns(xp)

    # Daily cap — only award what fits under the cap
    remaining_headroom = daily_cap - daily_xp_today
    if remaining_headroom <= 0:
        return 0
    xp = min(xp, remaining_headroom)

    return max(0, xp)


# ---------------------------------------------------------------------------
# Star grading
# ---------------------------------------------------------------------------

def grade_stars(correct_pct: float) -> int:
    """
    Convert a correctness percentage to a 1-3 star rating.

    Thresholds:
      - ≥60% → 1 star
      - ≥80% → 2 stars
      - 100% → 3 stars

    Args:
        correct_pct: Fraction of correct answers (0.0 to 1.0).

    Returns:
        Star count (0 if below threshold, 1-3 otherwise).
    """
    if correct_pct >= STAR_3_THRESHOLD:
        return 3
    if correct_pct >= STAR_2_THRESHOLD:
        return 2
    if correct_pct >= STAR_1_THRESHOLD:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Unlock logic
# ---------------------------------------------------------------------------

def check_unlock(
    prev_level_result: float,
    is_boss_level: bool,
    all_prev_stars: Sequence[int],
) -> bool:
    """
    Determine whether a level should be unlocked.

    Rules:
      - For normal levels: the immediately preceding level must have
        ≥60% correct (prev_level_result ≥ 0.60).
      - For boss levels: *all* preceding levels in the map must have
        at least 1 star (all values in ``all_prev_stars`` ≥ 1).

    Args:
        prev_level_result: Correctness ratio of the immediately preceding
            level (0.0-1.0). Use 1.0 for the first level (always unlocked).
        is_boss_level: Whether the level being unlocked is a boss level.
        all_prev_stars: Star ratings (0-3) for all levels preceding this
            one in the same map.

    Returns:
        True if the level should be unlocked.
    """
    if is_boss_level:
        # Boss levels require every prior level to have ≥1 star.
        return all(s >= 1 for s in all_prev_stars)
    return prev_level_result >= UNLOCK_CORRECT_THRESHOLD


# ---------------------------------------------------------------------------
# Streak computation
# ---------------------------------------------------------------------------

def update_streak(
    streak_history: Sequence[str],
    today: str,
) -> tuple[int, list[str]]:
    """
    Update the daily streak given the history and today's date.

    Logic:
      - If ``today`` is already in history, return unchanged (idempotent).
      - If the last entry was *yesterday*, increment the streak by 1.
      - If there's a gap, reset the streak to 1.
      - If history is empty, streak = 1.

    Args:
        streak_history: List of ISO 8601 date strings (YYYY-MM-DD), ordered.
        today: Today's date as ISO 8601 string (YYYY-MM-DD).

    Returns:
        Tuple of (new_streak_days, new_history_list).
    """
    today_date = _parse_date_safely(today)
    if today_date is None:
        # Invalid date — return unchanged
        return (len(streak_history), list(streak_history))

    history_set = set(streak_history)
    if today in history_set:
        # Idempotent: already counted today
        return (len(streak_history), list(streak_history))

    new_history = list(streak_history)

    if not new_history:
        new_history.append(today)
        return (1, new_history)

    last_date = _parse_date_safely(new_history[-1])
    if last_date is None:
        # Corrupt history — restart
        new_history.append(today)
        return (1, new_history)

    delta = (today_date - last_date).days
    if delta == 1:
        # Consecutive day
        new_history.append(today)
        return (len(new_history), new_history)
    elif delta <= 0:
        # Same day or somehow earlier (shouldn't happen normally)
        return (len(new_history), new_history)
    else:
        # Gap — reset streak
        new_history = [today]
        return (1, new_history)


def _parse_date_safely(date_str: str) -> date | None:
    """Parse an ISO 8601 date string safely, returning None on failure."""
    try:
        # Handle both date-only and full datetime strings
        return datetime.fromisoformat(date_str[:10]).date()
    except (ValueError, TypeError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Level computation
# ---------------------------------------------------------------------------

def compute_level(total_xp: int) -> int:
    """
    Compute the child's level (1-10) from total XP.

    Level thresholds:
      Lv.1:  0 XP
      Lv.2:  50 XP
      Lv.3:  120 XP
      Lv.4:  220 XP
      Lv.5:  350 XP
      Lv.6:  520 XP
      Lv.7:  730 XP
      Lv.8:  990 XP
      Lv.9:  1300 XP
      Lv.10: 1680 XP

    Args:
        total_xp: Total accumulated XP.

    Returns:
        Level from 1 to 10.
    """
    if total_xp < 0:
        return 1
    for i in range(len(LEVEL_THRESHOLDS) - 1, -1, -1):
        if total_xp >= LEVEL_THRESHOLDS[i]:
            return min(i + 1, MAX_LEVEL)
    return 1


def xp_for_next_level(current_level: int) -> int:
    """Return the XP threshold needed to reach the *next* level.

    Returns the last threshold if already at max level.
    """
    if current_level >= MAX_LEVEL:
        return LEVEL_THRESHOLDS[-1]
    return LEVEL_THRESHOLDS[current_level]  # current_level is 1-based, so index = level


def progress_to_next_level(total_xp: int) -> float:
    """Return progress ratio (0.0-1.0) toward the next level."""
    level = compute_level(total_xp)
    if level >= MAX_LEVEL:
        return 1.0
    current_threshold = LEVEL_THRESHOLDS[level - 1]
    next_threshold = LEVEL_THRESHOLDS[level]
    if next_threshold == current_threshold:
        return 1.0
    return (total_xp - current_threshold) / (next_threshold - current_threshold)


# ---------------------------------------------------------------------------
# Weekly report computation
# ---------------------------------------------------------------------------

def compute_weekly_report(progress: "ProgressState", days: int = 7) -> dict:
    """Compute weekly learning statistics from a ProgressState.

    Aggregates the past ``days`` days of activity by inspecting each level's
    ``last_played_at`` timestamp. Levels played within the window contribute
    to the report.

    This is a **pure function** — no IO. It reads only from the
    ``ProgressState`` data structure.

    Args:
        progress: The child's ProgressState (contains maps and levels).
        days: Number of days to look back (default 7).

    Returns:
        A dict with::

            {
                "levels_completed": int,   # levels cleared within window
                "xp_earned": int,          # estimated XP from cleared levels
                "avg_accuracy": float,     # mean best_correct_pct of active levels
                "active_days": int,        # distinct days with activity
                "streak_days": int,        # current streak from progress
            }
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)

    levels_completed = 0
    xp_earned = 0
    accuracies: list[float] = []
    active_dates: set[str] = set()

    for mp in progress.maps.values():
        for lp in mp.levels.values():
            if not lp.last_played_at:
                continue
            played = _parse_datetime_safely(lp.last_played_at)
            if played is None or played < window_start:
                continue

            # Track active day
            active_dates.add(played.date().isoformat())

            # Track accuracy for attempted levels
            if lp.attempts > 0:
                accuracies.append(lp.best_correct_pct)

            # Count completed levels
            if lp.cleared:
                levels_completed += 1
                # Estimate XP: 10 base per cleared level (simplified)
                xp_earned += 10 * max(1, lp.stars if hasattr(lp, "stars") else 1)

    # xp_earned fallback: use daily_xp if no per-level data
    if xp_earned == 0 and progress.daily_xp > 0:
        xp_earned = progress.daily_xp

    avg_accuracy = sum(accuracies) / len(accuracies) if accuracies else 0.0

    return {
        "levels_completed": levels_completed,
        "xp_earned": xp_earned,
        "avg_accuracy": round(avg_accuracy, 4),
        "active_days": len(active_dates),
        "streak_days": progress.streak_days,
    }


def identify_weak_topics(
    progress: "ProgressState",
    maps_data: "Sequence[QuestMap] | None" = None,
    threshold: float = 0.6,
) -> list[dict]:
    """Identify levels where the child's accuracy is below a threshold.

    Scans all played levels in the ProgressState and returns those with
    ``best_correct_pct`` below ``threshold``. Optionally enriches each
    entry with the level title from QuestMap metadata.

    This is a **pure function** — if title information is needed, pass
    ``maps_data`` (a sequence of QuestMap objects). No file IO is performed.

    Args:
        progress: The child's ProgressState.
        maps_data: Optional sequence of QuestMap objects for title lookup.
        threshold: Accuracy threshold below which a level is "weak" (0.0–1.0).

    Returns:
        A list of dicts, each::

            {
                "level_id": str,
                "map_id": str,
                "title": str,        # level title if available, else level_id
                "score_pct": float,  # the child's best correctness ratio
            }
    """
    # Build a lookup table for level titles from QuestMap data.
    title_lookup: dict[tuple[str, str], str] = {}
    if maps_data:
        for qmap in maps_data:
            for level in qmap.levels:
                title_lookup[(qmap.map_id, level.level_id)] = level.title

    weak: list[dict] = []
    for map_id, mp in progress.maps.items():
        for level_id, lp in mp.levels.items():
            if lp.attempts == 0:
                continue
            if lp.best_correct_pct < threshold:
                title = title_lookup.get((map_id, level_id), level_id)
                weak.append(
                    {
                        "level_id": level_id,
                        "map_id": map_id,
                        "title": title,
                        "score_pct": lp.best_correct_pct,
                    }
                )

    # Sort by accuracy ascending (worst first)
    weak.sort(key=lambda x: x["score_pct"])
    return weak


def recommend_review(
    progress: "ProgressState",
    weak_topics: list[dict],
    max_recommendations: int = 5,
) -> list[str]:
    """Recommend levels to review based on identified weak topics.

    Returns the ``level_id`` values from ``weak_topics``, limited to
    ``max_recommendations``. Each level is included only once.

    This is a **pure function**.

    Args:
        progress: The child's ProgressState (used for context; the main
            data source is ``weak_topics``).
        weak_topics: Output from :func:`identify_weak_topics`.
        max_recommendations: Maximum number of recommendations.

    Returns:
        A list of level IDs to review.
    """
    seen: set[str] = set()
    recommendations: list[str] = []
    for topic in weak_topics:
        level_id = topic.get("level_id", "")
        if level_id and level_id not in seen:
            seen.add(level_id)
            recommendations.append(level_id)
            if len(recommendations) >= max_recommendations:
                break
    return recommendations


def _parse_datetime_safely(dt_str: str) -> datetime | None:
    """Parse an ISO 8601 datetime string safely, returning None on failure."""
    try:
        result = datetime.fromisoformat(dt_str)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result
    except (ValueError, TypeError):
        return None


__all__ = [
    "LEVEL_THRESHOLDS",
    "MAX_LEVEL",
    "DEFAULT_DAILY_XP_CAP",
    "COMBO_MULTIPLIER",
    "BOSS_MULTIPLIER",
    "REPLAY_FACTOR",
    "STAR_1_THRESHOLD",
    "STAR_2_THRESHOLD",
    "STAR_3_THRESHOLD",
    "UNLOCK_CORRECT_THRESHOLD",
    "base_xp_for_correct",
    "apply_diminishing_returns",
    "apply_xp_cap",
    "compute_combo_bonus",
    "compute_boss_bonus",
    "award_xp",
    "grade_stars",
    "check_unlock",
    "update_streak",
    "compute_level",
    "xp_for_next_level",
    "progress_to_next_level",
    "compute_weekly_report",
    "identify_weak_topics",
    "recommend_review",
]
