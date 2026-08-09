"""
Tests for the ForgingService — question generation, schema validation,
and template fallback (no LLM mode).
"""

from __future__ import annotations

import pytest

from deeptutor.capabilities.quest.forging import (
    AGE_BAND_QUESTION_MATRIX,
    ForgingService,
    ValidationResult,
    check_answerability,
    validate_schema,
)
from deeptutor.services.gamification.models import Question


# ---------------------------------------------------------------------------
# validate_schema
# ---------------------------------------------------------------------------

class TestValidateSchema:
    def test_valid_single_choice(self):
        raw = {
            "question_type": "single_choice",
            "question_text": "What color is the sky?",
            "options": ["blue", "red", "green"],
            "correct_answer": "blue",
            "explanation": "The sky appears blue due to Rayleigh scattering.",
        }
        result = validate_schema(raw)
        assert result.valid is True
        assert result.question is not None
        assert result.question.question_type == "single_choice"
        assert result.question.correct_answer == "blue"

    def test_valid_true_false(self):
        raw = {
            "question_type": "true_false",
            "question_text": "The sun is a star.",
            "correct_answer": "true",
            "explanation": "The sun is indeed a star.",
        }
        result = validate_schema(raw)
        assert result.valid is True
        assert result.question is not None

    def test_valid_fill_blank(self):
        raw = {
            "question_type": "fill_blank",
            "question_text": "The capital of France is _____.",
            "correct_answer": "Paris",
            "acceptable_answers": ["paris", "Paris"],
            "explanation": "Paris is the capital of France.",
        }
        result = validate_schema(raw)
        assert result.valid is True
        assert "Paris" in result.question.acceptable_answers

    def test_unknown_question_type(self):
        raw = {
            "question_type": "nonexistent_type",
            "question_text": "Some text",
            "correct_answer": "A",
        }
        result = validate_schema(raw)
        assert result.valid is False
        assert any("Unknown question_type" in e for e in result.errors)

    def test_empty_question_text(self):
        raw = {
            "question_type": "single_choice",
            "question_text": "",
            "options": ["a", "b"],
            "correct_answer": "a",
        }
        result = validate_schema(raw)
        assert result.valid is False
        assert any("non-empty" in e for e in result.errors)

    def test_correct_answer_not_in_options(self):
        raw = {
            "question_type": "single_choice",
            "question_text": "What is 2+2?",
            "options": ["3", "5", "6"],
            "correct_answer": "4",
            "explanation": "2+2=4",
        }
        result = validate_schema(raw)
        assert result.valid is False
        assert any("not in options" in e for e in result.errors)

    def test_true_false_invalid_answer(self):
        raw = {
            "question_type": "true_false",
            "question_text": "Cats can fly.",
            "correct_answer": "maybe",
            "explanation": "Cats cannot fly.",
        }
        result = validate_schema(raw)
        assert result.valid is False
        assert any("true_false" in e.lower() or "true/false" in e.lower() for e in result.errors)

    def test_fill_blank_without_any_answer(self):
        raw = {
            "question_type": "fill_blank",
            "question_text": "Fill in: _____",
            "explanation": "No answer provided.",
        }
        result = validate_schema(raw)
        assert result.valid is False
        assert any("fill_blank" in e for e in result.errors)


# ---------------------------------------------------------------------------
# check_answerability
# ---------------------------------------------------------------------------

class TestCheckAnswerability:
    def test_answerable_single_choice(self):
        q = Question(
            text="What is 1+1?",
            question_type="single_choice",
            options=["1", "2", "3"],
            correct_answer="2",
        )
        assert check_answerability(q) is True

    def test_not_answerable_missing_correct_answer(self):
        q = Question(
            text="What is 1+1?",
            question_type="single_choice",
            options=["1", "2", "3"],
            correct_answer="99",
        )
        assert check_answerability(q) is False

    def test_answerable_fill_blank(self):
        q = Question(
            text="Fill: ___",
            question_type="fill_blank",
            correct_answer="answer",
        )
        assert check_answerability(q) is True

    def test_answerable_matching(self):
        q = Question(
            text="Match pairs",
            question_type="matching",
            matching_pairs=[["A", "1"], ["B", "2"]],
        )
        assert check_answerability(q) is True

    def test_not_answerable_empty_text(self):
        q = Question(text="", question_type="single_choice", options=["a", "b"], correct_answer="a")
        assert check_answerability(q) is False


# ---------------------------------------------------------------------------
# ForgingService — template mode (no LLM)
# ---------------------------------------------------------------------------

class TestForgingTemplateMode:
    @pytest.mark.asyncio
    async def test_generate_template_questions(self):
        service = ForgingService()
        chunks = [
            "Dinosaurs were amazing creatures. The T-Rex was a fierce predator. "
            "Triceratops had three horns. Stegosaurus had plates on its back.",
            "Dinosaur eggs were laid in nests. Baby dinosaurs hatched from eggs. "
            "Some dinosaurs ate plants while others ate meat.",
        ]
        questions = await service.generate_questions(
            chunks=chunks,
            age_band="7-9",
            count=3,
        )
        assert len(questions) > 0
        for q in questions:
            assert q.text
            assert q.question_type in AGE_BAND_QUESTION_MATRIX["7-9"]
            assert check_answerability(q)

    @pytest.mark.asyncio
    async def test_generate_with_default_content(self):
        service = ForgingService()
        questions = await service.generate_questions(
            chunks=["Hello world. This is a test passage about science and nature."],
            age_band="10-12",
            count=2,
        )
        assert len(questions) > 0

    @pytest.mark.asyncio
    async def test_template_fallback_on_llm_failure(self):
        """When LLM raises an exception, template questions should be generated."""
        class FailingLLM:
            async def complete(self, prompt, **kwargs):
                raise RuntimeError("LLM unavailable")

        service = ForgingService()
        chunks = ["Photosynthesis is how plants make food from sunlight. "
                   "Chlorophyll gives leaves their green color."]
        questions = await service.generate_questions(
            chunks=chunks,
            age_band="7-9",
            count=3,
            llm_client=FailingLLM(),
        )
        assert len(questions) > 0
        for q in questions:
            assert check_answerability(q)

    @pytest.mark.asyncio
    async def test_age_band_question_matrix_filtering(self):
        """7-9 age band should not generate fill_blank questions."""
        service = ForgingService()
        chunks = ["Animals are living things. Lions live in Africa. Fish swim in water."]
        questions = await service.generate_questions(
            chunks=chunks,
            age_band="7-9",
            count=5,
        )
        for q in questions:
            assert q.question_type in ["single_choice", "true_false", "image_choice"]


# ---------------------------------------------------------------------------
# ForgingService — LLM mode
# ---------------------------------------------------------------------------

class TestForgingLLMMode:
    @pytest.mark.asyncio
    async def test_generate_with_mock_llm(self):
        class MockLLM:
            async def complete(self, prompt, **kwargs):
                return (
                    '```json\n'
                    '{"questions": [\n'
                    '  {"question_type": "single_choice", "question_text": "What is 2+2?", '
                    '   "options": ["3", "4", "5"], "correct_answer": "4", '
                    '   "explanation": "2+2 equals 4.", "hints": ["Think about counting."]}\n'
                    ']}\n'
                    '```'
                )

        service = ForgingService()
        chunks = ["Math is fun. Numbers are everywhere."]
        questions = await service.generate_questions(
            chunks=chunks,
            age_band="10-12",
            count=1,
            llm_client=MockLLM(),
        )
        assert len(questions) >= 1
        assert questions[0].text == "What is 2+2?"
        assert questions[0].correct_answer == "4"

    @pytest.mark.asyncio
    async def test_llm_returns_invalid_json_falls_back_to_template(self):
        class BadJSONLLM:
            async def complete(self, prompt, **kwargs):
                return "This is not JSON at all."

        service = ForgingService()
        questions = await service.generate_questions(
            chunks=["The Earth orbits the Sun. The Moon orbits the Earth."],
            age_band="7-9",
            count=3,
            llm_client=BadJSONLLM(),
        )
        assert len(questions) > 0
