"""
Tests for the HintService — three-stage progressive hints.
"""

from __future__ import annotations

import pytest

from deeptutor.capabilities.quest.hints import HintService, STAGES, MAX_STAGE
from deeptutor.services.gamification.models import Question


class TestHintStages:
    def test_stage_constants(self):
        assert STAGES == ["encourage", "hint", "explain"]
        assert MAX_STAGE == 2

    def test_stage_0_encourage(self):
        service = HintService(language="zh")
        q = Question(text="What is 1+1?", question_type="single_choice",
                     options=["1", "2", "3"], correct_answer="2")
        hint = service.get_hint(q, 0)
        assert hint  # non-empty
        assert "💪" in hint or "🌟" in hint or "😊" in hint or "🚀" in hint

    def test_stage_1_structural_hint_with_options(self):
        service = HintService(language="zh")
        q = Question(text="What is 1+1?", question_type="single_choice",
                     options=["1", "2", "3"], correct_answer="2",
                     hints=["Count on your fingers!"])
        hint = service.get_hint(q, 1)
        assert hint == "Count on your fingers!"

    def test_stage_1_structural_hint_no_stored_hints(self):
        service = HintService(language="zh")
        q = Question(text="What is 1+1?", question_type="single_choice",
                     options=["1", "2", "3"], correct_answer="2", hints=[])
        hint = service.get_hint(q, 1)
        assert "提示" in hint

    def test_stage_2_explanation_from_question(self):
        service = HintService(language="zh")
        q = Question(
            text="What is 1+1?",
            question_type="single_choice",
            options=["1", "2", "3"],
            correct_answer="2",
            explanation="1+1=2, basic addition.",
        )
        hint = service.get_hint(q, 2)
        assert hint == "1+1=2, basic addition."

    def test_stage_2_default_explanation(self):
        service = HintService(language="zh")
        q = Question(
            text="What is 1+1?",
            question_type="single_choice",
            options=["1", "2", "3"],
            correct_answer="2",
            explanation="",
        )
        hint = service.get_hint(q, 2)
        assert "阅读材料" in hint  # default Chinese explanation

    def test_stage_clamped(self):
        """Stage beyond MAX_STAGE should be clamped to MAX_STAGE."""
        service = HintService(language="zh")
        q = Question(text="Test", question_type="true_false", correct_answer="true",
                     explanation="Explained.")
        hint = service.get_hint(q, 99)
        assert hint == "Explained."

    def test_negative_stage_clamped(self):
        service = HintService(language="zh")
        q = Question(text="Test", question_type="true_false", correct_answer="true")
        hint = service.get_hint(q, -5)
        assert hint  # should be encourage text


class TestHintLanguageEnglish:
    def test_english_encourage(self):
        service = HintService(language="en")
        q = Question(text="Test", question_type="true_false", correct_answer="true")
        hint = service.get_hint(q, 0)
        assert hint
        # Should contain an emoji (all encourage variants have one)
        assert any(ord(c) > 0x1F000 for c in hint)

    def test_english_structural_hint(self):
        service = HintService(language="en")
        q = Question(text="Test", question_type="single_choice",
                     options=["a", "b"], correct_answer="a", hints=[])
        hint = service.get_hint(q, 1)
        assert "Hint" in hint or "hint" in hint.lower()

    def test_english_default_explanation(self):
        service = HintService(language="en")
        q = Question(text="Test", question_type="true_false",
                     correct_answer="true", explanation="")
        hint = service.get_hint(q, 2)
        assert "reading material" in hint.lower()


class TestGetExplanation:
    @pytest.mark.asyncio
    async def test_no_llm_returns_stored_explanation(self):
        service = HintService(language="zh")
        q = Question(text="Test", question_type="single_choice",
                     options=["a", "b"], correct_answer="a",
                     explanation="Stored explanation.")
        result = await service.get_explanation(q)
        assert result == "Stored explanation."

    @pytest.mark.asyncio
    async def test_no_llm_no_explanation_returns_default(self):
        service = HintService(language="zh")
        q = Question(text="Test", question_type="single_choice",
                     options=["a", "b"], correct_answer="a", explanation="")
        result = await service.get_explanation(q)
        assert "阅读材料" in result

    @pytest.mark.asyncio
    async def test_with_llm(self):
        class MockLLM:
            async def complete(self, prompt, **kwargs):
                return "This is an LLM-generated explanation for children."

        service = HintService(language="zh")
        q = Question(text="Test", question_type="single_choice",
                     options=["a", "b"], correct_answer="a", explanation="Stored.")
        result = await service.get_explanation(q, llm_client=MockLLM())
        assert result == "This is an LLM-generated explanation for children."

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back(self):
        class FailingLLM:
            async def complete(self, prompt, **kwargs):
                raise RuntimeError("LLM error")

        service = HintService(language="zh")
        q = Question(text="Test", question_type="single_choice",
                     options=["a", "b"], correct_answer="a",
                     explanation="Safe fallback explanation.")
        result = await service.get_explanation(q, llm_client=FailingLLM())
        assert result == "Safe fallback explanation."
