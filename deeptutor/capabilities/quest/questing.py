"""
Questing Round — Turn-Based Level Completion
=============================================

Implements the core gameplay loop for a single level:
  1. Emit ``item_presented`` for each question.
  2. ``wait_for_input`` — pause for the child's answer.
  3. Grade the answer (deterministic).
  4. Emit ``item_judged``.
  5. On wrong answer: walk through 3-stage hints (encourage → hint → explain),
     allowing up to 2 additional attempts.
  6. After all questions: compute stars, XP, unlock, streak, badges.
  7. Emit ``level_cleared`` + any ``badge_earned`` events.
  8. Persist progress to the store.

All events use the existing ``StreamEventType`` members with a ``sub_type``
in metadata to distinguish quest-specific events (no enum changes).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from deeptutor.capabilities.quest.grading import GradingService, LevelResult
from deeptutor.capabilities.quest.hints import MAX_STAGE, HintService
from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.core.stream_bus import StreamBus
from deeptutor.services.gamification import badges as badge_engine
from deeptutor.services.gamification import engine as game_engine
from deeptutor.services.gamification import store as game_store
from deeptutor.services.gamification.models import (
    LevelProgress,
    MapProgress,
    Question,
)
from deeptutor.services.gamification.safety import SafetyFilter

_SOURCE = "kid_quest"

# Maximum extra attempts after the first wrong answer.
MAX_WRONG_ATTEMPTS: int = 2


def _now_iso() -> str:
    """Return current UTC time in ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def _question_to_event_dict(q: Question) -> dict[str, Any]:
    """Convert a Question to the dict payload for item_presented events."""
    return {
        "question_id": q.question_id,
        "text": q.text,
        "question_type": q.question_type,
        "options": q.options,
        "points": q.points,
    }


async def run_quest_round(
    questions: list[Question],
    stream: StreamBus,
    profile_id: str,
    level_id: str,
    map_key: str,
    age_band: str,
    hint_service: HintService | None = None,
    safety_filter: SafetyFilter | None = None,
    llm_client: Any | None = None,
    is_boss: bool = False,
    is_replay: bool = False,
    max_questions: int = 10,
) -> LevelResult:
    """Execute a single level round.

    Args:
        questions: The questions for this level.
        stream: The StreamBus to emit events on.
        profile_id: The child's profile ID.
        level_id: The level identifier.
        map_key: The map identifier (namespace key).
        age_band: Target age band.
        hint_service: Optional HintService instance.
        safety_filter: Optional SafetyFilter instance.
        llm_client: Optional LLM client for explanations.
        is_boss: Whether this is a boss level.
        is_replay: Whether the level was already cleared.
        max_questions: Maximum questions to ask (caps large question sets).

    Returns:
        A :class:`LevelResult` with the level's aggregate outcome.
    """
    hints = hint_service or HintService(language="zh")

    # Limit questions
    active_questions = questions[:max_questions]
    total = len(active_questions)

    if total == 0:
        return LevelResult(correct_count=0, total=0, score_pct=0.0, stars=0)

    # Track answers and per-question correctness
    answers: dict[str, str] = {}
    wrong_question_ids: list[str] = []
    correct_count = 0
    combo = 0
    max_combo = 0
    xp_earned = 0

    for idx, q in enumerate(active_questions):
        is_correct = False
        hint_stage = 0

        # Present the question
        await stream.emit(
            StreamEvent(
                type=StreamEventType.CONTENT,
                source=_SOURCE,
                stage="questing",
                content=q.text,
                metadata={
                    "sub_type": "item_presented",
                    "question": _question_to_event_dict(q),
                    "index": idx,
                    "total": total,
                },
            )
        )

        # Answer loop: initial attempt + up to MAX_WRONG_ATTEMPTS retries
        for attempt in range(MAX_WRONG_ATTEMPTS + 1):
            user_answer = await stream.wait_for_input(
                prompt=f"Question {idx + 1}/{total}",
                source=_SOURCE,
                stage="questing",
                timeout=None,
            )

            user_answer = (user_answer or "").strip()

            # Grade
            is_correct = GradingService.grade_single(q, user_answer)

            # Emit judgment
            await stream.emit(
                StreamEvent(
                    type=StreamEventType.CONTENT,
                    source=_SOURCE,
                    stage="questing",
                    metadata={
                        "sub_type": "item_judged",
                        "question_id": q.question_id,
                        "user_answer": user_answer,
                        "is_correct": is_correct,
                        "attempt": attempt,
                    },
                )
            )

            if is_correct:
                answers[q.question_id] = user_answer
                combo += 1
                max_combo = max(max_combo, combo)
                correct_count += 1
                xp_earned += q.points
                break

            # Wrong answer: provide hint if retries remain
            if attempt < MAX_WRONG_ATTEMPTS:
                hint_text = hints.get_hint(q, hint_stage)
                hint_stage = min(hint_stage + 1, MAX_STAGE)
                await stream.content(
                    text=hint_text,
                    source=_SOURCE,
                    stage="questing",
                    metadata={"sub_type": "hint", "stage": hint_stage - 1},
                )
            else:
                # No more attempts — record the final answer
                answers[q.question_id] = user_answer
                wrong_question_ids.append(q.question_id)
                combo = 0
                # Show the explanation
                explanation = hints.get_hint(q, MAX_STAGE)
                await stream.content(
                    text=explanation,
                    source=_SOURCE,
                    stage="questing",
                    metadata={"sub_type": "explanation", "question_id": q.question_id},
                )

    # Compute aggregate result
    score_pct = correct_count / total if total > 0 else 0.0
    stars = game_engine.grade_stars(score_pct)

    # Apply engine multipliers for XP
    adjusted_xp = game_engine.award_xp(
        current_xp=0,
        correct_count=correct_count,
        combo=max_combo,
        is_boss=is_boss,
        is_replay=is_replay,
    )

    result = LevelResult(
        correct_count=correct_count,
        total=total,
        score_pct=score_pct,
        stars=stars,
        xp_earned=adjusted_xp,
        wrong_question_ids=wrong_question_ids,
        answers=answers,
    )

    # --- Settlement: update ProgressState ---
    await _settle_level(
        stream=stream,
        profile_id=profile_id,
        level_id=level_id,
        map_key=map_key,
        result=result,
        max_combo=max_combo,
        is_boss=is_boss,
    )

    return result


async def _settle_level(
    stream: StreamBus,
    profile_id: str,
    level_id: str,
    map_key: str,
    result: LevelResult,
    max_combo: int,
    is_boss: bool,
) -> None:
    """Update gamification state, emit level_cleared and badge events, persist.

    Args:
        stream: The StreamBus.
        profile_id: The child's profile.
        level_id: The completed level.
        map_key: The map namespace key.
        result: The level result.
        max_combo: Best combo achieved during the round.
        is_boss: Whether this was a boss level.
    """
    # Load current state
    state = game_store.load(profile_id)
    if state is None:
        state = game_store.init_if_absent(profile_id)

    now = _now_iso()
    today = now[:10]

    # Daily XP reset check
    if state.daily_xp_date != today:
        state.daily_xp = 0
        state.daily_xp_date = today

    # Update XP
    state.total_xp += result.xp_earned
    state.daily_xp += result.xp_earned

    # Recompute level
    state.level = game_engine.compute_level(state.total_xp)

    # Update combo record
    state.max_combo = max(state.max_combo, max_combo)

    # Update streak
    streak_days, streak_history = game_engine.update_streak(
        state.streak_history, today
    )
    state.streak_days = streak_days
    state.streak_history = streak_history

    # Update map/level progress
    map_progress = state.maps.get(map_key)
    if map_progress is None:
        map_progress = MapProgress(map_id=map_key, unlocked=True)
        state.maps[map_key] = map_progress

    level_progress = map_progress.levels.get(level_id)
    if level_progress is None:
        level_progress = LevelProgress(level_id=level_id)
        map_progress.levels[level_id] = level_progress

    level_progress.attempts += 1
    level_progress.stars = max(level_progress.stars, result.stars)
    level_progress.best_correct_pct = max(level_progress.best_correct_pct, result.score_pct)
    level_progress.cleared = level_progress.cleared or (result.score_pct >= game_engine.STAR_1_THRESHOLD)
    level_progress.last_played_at = now

    # Check map completion
    if all(lp.cleared for lp in map_progress.levels.values()) and map_progress.levels:
        map_progress.completed = True

    # Daily goal check
    if state.daily_xp >= state.daily_goal_xp and today not in state.daily_goal_met_dates:
        state.daily_goal_met_dates.append(today)

    # Badge check
    new_badges = badge_engine.check_all(state)
    state.badges.extend(new_badges)

    # Persist
    game_store.save(profile_id, state)

    # --- Emit events ---

    # level_cleared
    await stream.emit(
        StreamEvent(
            type=StreamEventType.RESULT,
            source=_SOURCE,
            stage="questing",
            metadata={
                "sub_type": "level_cleared",
                "level_id": level_id,
                "map_key": map_key,
                "stars": result.stars,
                "score_pct": result.score_pct,
                "correct_count": result.correct_count,
                "total": result.total,
                "xp_earned": result.xp_earned,
                "total_xp": state.total_xp,
                "level": state.level,
                "max_combo": max_combo,
                "streak_days": state.streak_days,
            },
        )
    )

    # badge_earned (one event per badge)
    for badge_id in new_badges:
        badge_rule = badge_engine.get_badge_rule(badge_id)
        badge_info = {}
        if badge_rule is not None:
            badge_info = {
                "name": badge_rule.name,
                "description": badge_rule.description,
                "icon": badge_rule.icon,
            }
        await stream.emit(
            StreamEvent(
                type=StreamEventType.RESULT,
                source=_SOURCE,
                stage="questing",
                metadata={
                    "sub_type": "badge_earned",
                    "badge_id": badge_id,
                    **badge_info,
                },
            )
        )


__all__ = [
    "run_quest_round",
    "MAX_WRONG_ATTEMPTS",
]
