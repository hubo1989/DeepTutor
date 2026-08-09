"""
Tests for the questing round — turn-based level completion with mock StreamBus.

Simulates the user input sequence by pre-feeding answers via submit_input.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deeptutor.capabilities.quest.grading import LevelResult
from deeptutor.capabilities.quest.hints import HintService
from deeptutor.capabilities.quest.questing import MAX_WRONG_ATTEMPTS, run_quest_round
from deeptutor.core.stream import StreamEventType
from deeptutor.core.stream_bus import StreamBus
from deeptutor.services.gamification.models import Question

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockStreamBus(StreamBus):
    """A StreamBus subclass that auto-feeds pre-queued answers.

    Instead of blocking on wait_for_input, it returns the next answer
    from the queue immediately.
    """

    def __init__(self, answer_queue: list[str]) -> None:
        super().__init__()
        self._answer_queue = list(answer_queue)
        self._answer_idx = 0
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)
        await super().emit(event)

    async def wait_for_input(self, prompt="", source="", stage="", timeout=None) -> str:
        if self._answer_idx < len(self._answer_queue):
            answer = self._answer_queue[self._answer_idx]
            self._answer_idx += 1
            return answer
        return ""


@pytest.fixture
def temp_gamification_dir(tmp_path, monkeypatch):
    """Redirect gamification data to a temp path."""
    import deeptutor.services.gamification.store as store_mod
    gamedir = tmp_path / "gamification"
    gamedir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store_mod, "_GAMIFICATION_DIR", gamedir)
    return gamedir


@pytest.fixture
def simple_questions():
    return [
        Question(
            question_id="q1",
            text="What is 2+2?",
            question_type="single_choice",
            options=["3", "4", "5"],
            correct_answer="4",
            explanation="2+2=4",
            points=10,
            hints=["Think about counting."],
        ),
        Question(
            question_id="q2",
            text="Is the sky blue?",
            question_type="true_false",
            correct_answer="true",
            explanation="Yes, the sky is blue.",
            points=10,
        ),
    ]


# ---------------------------------------------------------------------------
# Basic quest round
# ---------------------------------------------------------------------------

class TestQuestRound:
    @pytest.mark.asyncio
    async def test_all_correct(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        hint_service = HintService(language="zh")

        result = await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-kid-1",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
            hint_service=hint_service,
        )

        assert result.correct_count == 2
        assert result.total == 2
        assert result.score_pct == 1.0
        assert result.stars == 3  # 100% → 3 stars
        assert result.wrong_question_ids == []

    @pytest.mark.asyncio
    async def test_partial_correct(self, temp_gamification_dir, simple_questions):
        # q1 correct, q2 wrong (all attempts)
        bus = MockStreamBus(answer_queue=["4", "false", "false", "false"])
        hint_service = HintService(language="zh")

        result = await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-kid-2",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
            hint_service=hint_service,
        )

        assert result.correct_count == 1
        assert result.total == 2
        assert result.score_pct == 0.5
        assert "q2" in result.wrong_question_ids

    @pytest.mark.asyncio
    async def test_wrong_then_correct_with_retry(self, temp_gamification_dir, simple_questions):
        # q1: wrong, then correct on retry; q2: correct
        bus = MockStreamBus(answer_queue=["3", "4", "true"])
        hint_service = HintService(language="zh")

        result = await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-kid-3",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
            hint_service=hint_service,
        )

        assert result.correct_count == 2
        assert result.total == 2

    @pytest.mark.asyncio
    async def test_empty_questions(self, temp_gamification_dir):
        bus = MockStreamBus(answer_queue=[])
        result = await run_quest_round(
            questions=[],
            stream=bus,
            profile_id="test-kid-empty",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        assert result.correct_count == 0
        assert result.total == 0


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------

class TestEventEmission:
    @pytest.mark.asyncio
    async def test_item_presented_emitted(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-events-1",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        # Check item_presented events exist
        presented = [
            e for e in bus.events
            if e.type == StreamEventType.CONTENT
            and e.metadata.get("sub_type") == "item_presented"
        ]
        assert len(presented) == 2

    @pytest.mark.asyncio
    async def test_item_judged_emitted(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-events-2",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        judged = [
            e for e in bus.events
            if e.type == StreamEventType.CONTENT
            and e.metadata.get("sub_type") == "item_judged"
        ]
        assert len(judged) >= 2
        assert judged[0].metadata["is_correct"] is True

    @pytest.mark.asyncio
    async def test_level_cleared_emitted(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-events-3",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        cleared = [
            e for e in bus.events
            if e.type == StreamEventType.RESULT
            and e.metadata.get("sub_type") == "level_cleared"
        ]
        assert len(cleared) == 1
        assert cleared[0].metadata["stars"] == 3
        assert cleared[0].metadata["correct_count"] == 2

    @pytest.mark.asyncio
    async def test_hint_emitted_on_wrong(self, temp_gamification_dir, simple_questions):
        # q1 wrong on first attempt, then correct
        bus = MockStreamBus(answer_queue=["3", "4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-events-4",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        hints = [
            e for e in bus.events
            if e.metadata.get("sub_type") == "hint"
        ]
        assert len(hints) >= 1  # at least one hint emitted

    @pytest.mark.asyncio
    async def test_explanation_emitted_after_exhausting_attempts(self, temp_gamification_dir, simple_questions):
        # q1 all wrong → explanation shown
        bus = MockStreamBus(answer_queue=["3", "3", "3", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-events-5",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        explanations = [
            e for e in bus.events
            if e.metadata.get("sub_type") == "explanation"
        ]
        assert len(explanations) >= 1


# ---------------------------------------------------------------------------
# Gamification state update
# ---------------------------------------------------------------------------

class TestStateUpdate:
    @pytest.mark.asyncio
    async def test_xp_persisted(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-state-1",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        from deeptutor.services.gamification.store import load
        state = load("test-state-1")
        assert state is not None
        assert state.total_xp > 0  # earned some XP
        assert state.level >= 1

    @pytest.mark.asyncio
    async def test_streak_updated(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-state-2",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        from deeptutor.services.gamification.store import load
        state = load("test-state-2")
        assert state is not None
        assert state.streak_days >= 1

    @pytest.mark.asyncio
    async def test_level_progress_recorded(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-state-3",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        from deeptutor.services.gamification.store import load
        state = load("test-state-3")
        assert state is not None
        assert "test_map" in state.maps
        lp = state.maps["test_map"].levels.get("level_1")
        assert lp is not None
        assert lp.attempts == 1
        assert lp.stars == 3

    @pytest.mark.asyncio
    async def test_first_clear_badge(self, temp_gamification_dir, simple_questions):
        bus = MockStreamBus(answer_queue=["4", "true"])
        await run_quest_round(
            questions=simple_questions,
            stream=bus,
            profile_id="test-state-4",
            level_id="level_1",
            map_key="test_map",
            age_band="7-9",
        )
        from deeptutor.services.gamification.store import load
        state = load("test-state-4")
        assert state is not None
        # First clear badge should be earned (60%+ → cleared)
        assert "first_clear" in state.badges
