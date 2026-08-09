"""
Tests for the GradingService — deterministic grading for all question types.

Key axiom: the same question + same answer MUST always produce the same result.
"""

from __future__ import annotations

import json

import pytest

from deeptutor.capabilities.quest.grading import GradingService, LevelResult
from deeptutor.services.gamification.models import Question

# ---------------------------------------------------------------------------
# Single choice
# ---------------------------------------------------------------------------


class TestSingleChoiceGrading:
    def test_correct(self):
        q = Question(
            text="What color is grass?",
            question_type="single_choice",
            options=["green", "blue", "red"],
            correct_answer="green",
        )
        assert GradingService.grade_single(q, "green") is True

    def test_incorrect(self):
        q = Question(
            text="What color is grass?",
            question_type="single_choice",
            options=["green", "blue", "red"],
            correct_answer="green",
        )
        assert GradingService.grade_single(q, "blue") is False

    def test_empty_answer(self):
        q = Question(
            text="What color is grass?",
            question_type="single_choice",
            options=["green", "blue"],
            correct_answer="green",
        )
        assert GradingService.grade_single(q, "") is False

    def test_whitespace_stripped(self):
        q = Question(
            text="What color is grass?",
            question_type="single_choice",
            options=["green", "blue"],
            correct_answer="green",
        )
        assert GradingService.grade_single(q, "  green  ") is True


# ---------------------------------------------------------------------------
# True / False
# ---------------------------------------------------------------------------


class TestTrueFalseGrading:
    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("对", True),
            ("正确", True),
            ("false", False),
            ("False", False),
            ("错", False),
            ("错误", False),
        ],
    )
    def test_various_inputs(self, answer, expected):
        q = Question(
            text="The sun is hot.",
            question_type="true_false",
            correct_answer="true" if expected else "false",
        )
        assert GradingService.grade_single(q, answer) is True

    def test_wrong_answer(self):
        q = Question(
            text="Fish can fly.",
            question_type="true_false",
            correct_answer="false",
        )
        assert GradingService.grade_single(q, "true") is False

    def test_cross_variant_matching(self):
        """true vs 对 should both be treated as correct if the question's answer is 'true'."""
        q = Question(
            text="The Earth is round.",
            question_type="true_false",
            correct_answer="true",
        )
        assert GradingService.grade_single(q, "对") is True


# ---------------------------------------------------------------------------
# Fill blank
# ---------------------------------------------------------------------------


class TestFillBlankGrading:
    def test_exact_match(self):
        q = Question(
            text="The capital of France is ____.",
            question_type="fill_blank",
            correct_answer="Paris",
            acceptable_answers=["paris", "Paris", "PARIS"],
        )
        assert GradingService.grade_single(q, "Paris") is True

    def test_case_insensitive(self):
        q = Question(
            text="The capital of France is ____.",
            question_type="fill_blank",
            correct_answer="Paris",
            acceptable_answers=["paris"],
        )
        assert GradingService.grade_single(q, "PARIS") is True

    def test_wrong_answer(self):
        q = Question(
            text="The capital of France is ____.",
            question_type="fill_blank",
            correct_answer="Paris",
            acceptable_answers=["paris"],
        )
        assert GradingService.grade_single(q, "London") is False

    def test_acceptable_answers_include_correct(self):
        """correct_answer should always be in the acceptable set."""
        q = Question(
            text="Fill: ___",
            question_type="fill_blank",
            correct_answer="answer",
            acceptable_answers=[],
        )
        assert GradingService.grade_single(q, "answer") is True


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class TestMatchingGrading:
    def test_correct_order_independent(self):
        q = Question(
            text="Match capitals to countries.",
            question_type="matching",
            matching_pairs=[["France", "Paris"], ["Japan", "Tokyo"]],
        )
        # Same pairs, different order
        user_answer = json.dumps([["Japan", "Tokyo"], ["France", "Paris"]])
        assert GradingService.grade_single(q, user_answer) is True

    def test_wrong_matching(self):
        q = Question(
            text="Match capitals to countries.",
            question_type="matching",
            matching_pairs=[["France", "Paris"], ["Japan", "Tokyo"]],
        )
        user_answer = json.dumps([["France", "Tokyo"], ["Japan", "Paris"]])
        assert GradingService.grade_single(q, user_answer) is False


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestOrderingGrading:
    def test_correct_order(self):
        q = Question(
            text="Put these in order: 1, 2, 3",
            question_type="ordering",
            ordering_sequence=["1", "2", "3"],
        )
        user_answer = json.dumps(["1", "2", "3"])
        assert GradingService.grade_single(q, user_answer) is True

    def test_wrong_order(self):
        q = Question(
            text="Put these in order: 1, 2, 3",
            question_type="ordering",
            ordering_sequence=["1", "2", "3"],
        )
        user_answer = json.dumps(["3", "2", "1"])
        assert GradingService.grade_single(q, user_answer) is False

    def test_comma_separated(self):
        q = Question(
            text="Order these events.",
            question_type="ordering",
            ordering_sequence=["A", "B", "C"],
        )
        assert GradingService.grade_single(q, "A, B, C") is True


# ---------------------------------------------------------------------------
# Error correction
# ---------------------------------------------------------------------------


class TestErrorCorrectionGrading:
    def test_correct_by_id(self):
        q = Question(
            text="Which word is spelled wrong?",
            question_type="error_correction",
            options=["apple", "banana", "kat", "dog"],
            correct_answer="kat",
            correct_answer_ids=["2"],
        )
        assert GradingService.grade_single(q, "2") is True

    def test_correct_by_text(self):
        q = Question(
            text="Which word is spelled wrong?",
            question_type="error_correction",
            options=["apple", "banana", "kat", "dog"],
            correct_answer="kat",
            correct_answer_ids=["2"],
        )
        assert GradingService.grade_single(q, "kat") is True

    def test_wrong(self):
        q = Question(
            text="Which word is spelled wrong?",
            question_type="error_correction",
            options=["apple", "banana", "kat", "dog"],
            correct_answer="kat",
            correct_answer_ids=["2"],
        )
        assert GradingService.grade_single(q, "0") is False


# ---------------------------------------------------------------------------
# Boss comprehensive
# ---------------------------------------------------------------------------


class TestBossComprehensiveGrading:
    def test_all_sub_correct(self):
        q = Question(
            text="Big boss question",
            question_type="boss_comprehensive",
            sub_questions=[
                {"question_type": "single_choice", "options": ["A", "B"], "correct_answer": "A"},
                {"question_type": "true_false", "correct_answer": "true"},
            ],
        )
        user_answer = json.dumps(["A", "true"])
        assert GradingService.grade_single(q, user_answer) is True

    def test_one_sub_wrong(self):
        q = Question(
            text="Big boss question",
            question_type="boss_comprehensive",
            sub_questions=[
                {"question_type": "single_choice", "options": ["A", "B"], "correct_answer": "A"},
                {"question_type": "true_false", "correct_answer": "true"},
            ],
        )
        user_answer = json.dumps(["A", "false"])
        assert GradingService.grade_single(q, user_answer) is False


# ---------------------------------------------------------------------------
# Determinism test (100x same answer → same result)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_deterministic_100_runs(self):
        """Same question + same answer must always produce the same result (100 runs)."""
        q = Question(
            text="What is the sky color?",
            question_type="single_choice",
            options=["blue", "green", "red"],
            correct_answer="blue",
        )
        results = [GradingService.grade_single(q, "blue") for _ in range(100)]
        assert all(r is True for r in results)

        results_wrong = [GradingService.grade_single(q, "red") for _ in range(100)]
        assert all(r is False for r in results_wrong)


# ---------------------------------------------------------------------------
# grade_level
# ---------------------------------------------------------------------------


class TestGradeLevel:
    def test_all_correct(self):
        questions = [
            Question(
                question_id="q1",
                text="Q1",
                question_type="single_choice",
                options=["a", "b"],
                correct_answer="a",
                points=10,
            ),
            Question(
                question_id="q2",
                text="Q2",
                question_type="single_choice",
                options=["x", "y"],
                correct_answer="x",
                points=10,
            ),
        ]
        answers = {"q1": "a", "q2": "x"}
        result = GradingService.grade_level(questions, answers)
        assert result.correct_count == 2
        assert result.total == 2
        assert result.score_pct == 1.0
        assert result.stars == 3  # 100% → 3 stars
        assert result.xp_earned == 20
        assert result.wrong_question_ids == []

    def test_partial(self):
        questions = [
            Question(
                question_id="q1",
                text="Q1",
                question_type="single_choice",
                options=["a", "b"],
                correct_answer="a",
                points=10,
            ),
            Question(
                question_id="q2",
                text="Q2",
                question_type="single_choice",
                options=["x", "y"],
                correct_answer="x",
                points=10,
            ),
        ]
        answers = {"q1": "a", "q2": "y"}  # q2 wrong
        result = GradingService.grade_level(questions, answers)
        assert result.correct_count == 1
        assert result.total == 2
        assert result.score_pct == 0.5
        assert result.stars == 0  # below 60%
        assert "q2" in result.wrong_question_ids

    def test_empty_questions(self):
        result = GradingService.grade_level([], {})
        assert result.correct_count == 0
        assert result.total == 0
