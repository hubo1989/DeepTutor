"""
Grading Service — Deterministic Question Scoring
================================================

**This module NEVER calls an LLM.**  Grading is 100% deterministic: given
the same question and the same answer, the result is always identical.

This is the *judgment axiom* of the gamification system — fairness is
guaranteed by making the grade function a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deeptutor.services.gamification.models import Question


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class LevelResult:
    """Aggregate grading result for a full level (all questions).

    Attributes:
        correct_count: Number of correctly answered questions.
        total: Total questions in the level.
        score_pct: Correctness ratio (0.0 – 1.0).
        stars: Star rating (0–3) computed from ``score_pct``.
        xp_earned: Base XP earned (before multipliers).
        wrong_question_ids: IDs of questions answered incorrectly.
        answers: Map of question_id → user_answer.
    """

    correct_count: int = 0
    total: int = 0
    score_pct: float = 0.0
    stars: int = 0
    xp_earned: int = 0
    wrong_question_ids: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# GradingService
# ---------------------------------------------------------------------------

class GradingService:
    """Deterministic, zero-LLM grading for all question types."""

    @staticmethod
    def grade_single(q: Question, user_answer: str) -> bool:
        """Grade a single question against the user's answer.

        The grading logic is purely deterministic — no LLM, no fuzzy
        matching, no randomness.

        Args:
            q: The question to grade.
            user_answer: The user's answer string.

        Returns:
            ``True`` if the answer is correct.
        """
        if not user_answer:
            return False

        ua = user_answer.strip()
        q_type = q.question_type

        if q_type in ("single_choice", "image_choice"):
            return ua == q.correct_answer.strip()

        if q_type == "true_false":
            ua_lower = ua.lower()
            ca_lower = q.correct_answer.strip().lower()
            # Normalize Chinese variants
            true_variants = {"true", "对", "正确", "t", "yes", "y", "1"}
            false_variants = {"false", "错", "错误", "f", "no", "n", "0"}
            ua_in_true = ua_lower in true_variants
            ca_in_true = ca_lower in true_variants
            ua_in_false = ua_lower in false_variants
            ca_in_false = ca_lower in false_variants
            return (ua_in_true and ca_in_true) or (ua_in_false and ca_in_false)

        if q_type == "fill_blank":
            ua_lower = ua.lower()
            acceptable = {a.strip().lower() for a in q.acceptable_answers}
            acceptable.add(q.correct_answer.strip().lower())
            return ua_lower in acceptable

        if q_type == "matching":
            # user_answer is a JSON string like [["A","1"],["B","2"]]
            import json as _json

            try:
                ua_pairs = _json.loads(ua)
            except (ValueError, TypeError):
                # Fall back to direct comparison
                return ua == q.correct_answer
            if not isinstance(ua_pairs, list):
                return False

            # Normalize both sides as sets of frozensets (order-independent)
            ua_set = set()
            for pair in ua_pairs:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    ua_set.add(frozenset([str(pair[0]).strip(), str(pair[1]).strip()]))

            ca_set = set()
            for pair in q.matching_pairs:
                if len(pair) >= 2:
                    ca_set.add(frozenset([str(pair[0]).strip(), str(pair[1]).strip()]))

            return ua_set == ca_set

        if q_type == "ordering":
            import json as _json

            try:
                ua_list = _json.loads(ua)
            except (ValueError, TypeError):
                # Maybe comma-separated
                ua_list = [s.strip() for s in ua.split(",")]
            if not isinstance(ua_list, list):
                return False
            ua_normalized = [str(s).strip() for s in ua_list]
            ca_normalized = [str(s).strip() for s in q.ordering_sequence]
            return ua_normalized == ca_normalized

        if q_type == "error_correction":
            # user_answer is one of the option indices (or the option text)
            ua_stripped = ua.strip()
            ca_ids = {str(i).strip() for i in q.correct_answer_ids}
            # Check if user_answer matches one of the correct answer ids
            if ua_stripped in ca_ids:
                return True
            # Also check against correct_answer text
            if ua_stripped == q.correct_answer.strip():
                return True
            # Check if user answer text is in correct_answer_ids (when they store text)
            return ua_stripped in ca_ids

        if q_type == "boss_comprehensive":
            # Grade each sub-question; all must be correct
            import json as _json

            try:
                ua_subs = _json.loads(ua)
            except (ValueError, TypeError):
                ua_subs = [ua]
            if not isinstance(ua_subs, list):
                ua_subs = [ua]

            for i, sub_q in enumerate(q.sub_questions):
                if not isinstance(sub_q, dict):
                    continue
                sub_question = Question(
                    question_id=sub_q.get("question_id", f"sub_{i}"),
                    text=sub_q.get("question_text", sub_q.get("text", "")),
                    question_type=sub_q.get("question_type", "single_choice"),
                    options=sub_q.get("options", []),
                    correct_answer=sub_q.get("correct_answer", ""),
                    acceptable_answers=sub_q.get("acceptable_answers", []),
                    correct_answer_ids=sub_q.get("correct_answer_ids", []),
                    matching_pairs=sub_q.get("matching_pairs", []),
                    ordering_sequence=sub_q.get("ordering_sequence", []),
                )
                sub_answer = ua_subs[i] if i < len(ua_subs) else ""
                if not GradingService.grade_single(sub_question, str(sub_answer)):
                    return False
            return True

        # Default: exact match
        return ua == q.correct_answer.strip()

    @staticmethod
    def grade_level(
        questions: list[Question],
        answers: dict[str, str],
    ) -> LevelResult:
        """Grade all questions in a level.

        Args:
            questions: List of questions in the level.
            answers: Map of ``question_id → user_answer``.

        Returns:
            A :class:`LevelResult` with aggregate statistics.
        """
        from deeptutor.services.gamification.engine import grade_stars

        total = len(questions)
        if total == 0:
            return LevelResult(correct_count=0, total=0, score_pct=0.0, stars=0)

        correct_count = 0
        wrong_ids: list[str] = []
        xp = 0

        for q in questions:
            ua = answers.get(q.question_id, "")
            if GradingService.grade_single(q, ua):
                correct_count += 1
                xp += q.points
            else:
                wrong_ids.append(q.question_id)

        score_pct = correct_count / total
        stars = grade_stars(score_pct)

        return LevelResult(
            correct_count=correct_count,
            total=total,
            score_pct=score_pct,
            stars=stars,
            xp_earned=xp,
            wrong_question_ids=wrong_ids,
            answers=answers,
        )


__all__ = [
    "GradingService",
    "LevelResult",
]
