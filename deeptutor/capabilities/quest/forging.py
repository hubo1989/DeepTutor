"""
Forging Service — Question Generation
=====================================

Generates age-appropriate questions from source material (corpus chunks).

Two modes:
  1. **LLM mode** — when an ``llm_client`` is provided, the service sends
     a structured prompt to the LLM and parses the JSON response into
     :class:`Question` objects.
  2. **Template fallback** — when no LLM is available, the service extracts
     keywords from the corpus text and builds simple multiple-choice /
     true-false questions so the pipeline can still run.

The service supports eight question types, restricted by age band via
:data:`AGE_BAND_QUESTION_MATRIX`.

Validation is strict: every question is schema-checked via
:meth:`validate_schema` before it is accepted.  Failed validation triggers
up to 2 regeneration attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Sequence

from deeptutor.services.gamification.models import Question
from deeptutor.services.gamification.safety import SafetyFilter

# ---------------------------------------------------------------------------
# Question type definitions
# ---------------------------------------------------------------------------

# The eight question types supported by the system.
QUESTION_TYPES: dict[str, dict[str, Any]] = {
    "single_choice": {
        "label": "Single Choice",
        "needs_options": True,
        "min_options": 2,
        "max_options_default": 4,
    },
    "image_choice": {
        "label": "Image Choice",
        "needs_options": True,
        "min_options": 2,
        "max_options_default": 3,
    },
    "true_false": {
        "label": "True / False",
        "needs_options": False,
        "min_options": 0,
        "max_options_default": 0,
    },
    "fill_blank": {
        "label": "Fill in the Blank",
        "needs_options": False,
        "min_options": 0,
        "max_options_default": 0,
    },
    "matching": {
        "label": "Matching",
        "needs_options": False,
        "min_options": 0,
        "max_options_default": 0,
    },
    "ordering": {
        "label": "Ordering",
        "needs_options": False,
        "min_options": 0,
        "max_options_default": 0,
    },
    "error_correction": {
        "label": "Error Correction",
        "needs_options": True,
        "min_options": 3,
        "max_options_default": 4,
    },
    "boss_comprehensive": {
        "label": "Boss Comprehensive",
        "needs_options": False,
        "min_options": 0,
        "max_options_default": 0,
    },
}

# Age-band → supported question types.
AGE_BAND_QUESTION_MATRIX: dict[str, list[str]] = {
    "7-9": ["single_choice", "true_false", "image_choice"],
    "10-12": ["single_choice", "true_false", "fill_blank", "matching", "ordering"],
    "13-15": [
        "single_choice",
        "true_false",
        "fill_blank",
        "matching",
        "ordering",
        "error_correction",
        "boss_comprehensive",
    ],
}

# Maximum options per age band.
AGE_BAND_MAX_OPTIONS: dict[str, int] = {
    "7-9": 3,
    "10-12": 4,
    "13-15": 4,
}

MAX_REGENERATION_ATTEMPTS: int = 2


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Result of validating a raw question dictionary.

    Attributes:
        valid: Whether the question passes schema validation.
        errors: List of validation error messages.
        question: The validated :class:`Question` (only when ``valid``).
    """

    valid: bool = False
    errors: list[str] = field(default_factory=list)
    question: Question | None = None


def validate_schema(raw: dict[str, Any]) -> ValidationResult:
    """Validate a raw question dict against the expected schema.

    Checks:
      - ``question_type`` is a known type.
      - ``question_text`` is non-empty.
      - For option-based types: ``options`` is a list of ≥ 2 strings.
      - ``correct_answer`` is present.
      - For single_choice / image_choice: ``correct_answer`` ∈ ``options``.
      - For true_false: ``correct_answer`` ∈ ``{"true", "false"}``.
      - For fill_blank: ``acceptable_answers`` or ``correct_answer`` present.

    Args:
        raw: The raw dictionary to validate.

    Returns:
        A :class:`ValidationResult`.
    """
    errors: list[str] = []

    q_type = str(raw.get("question_type", "")).strip()
    if q_type not in QUESTION_TYPES:
        errors.append(f"Unknown question_type: {q_type!r}")

    q_text = str(raw.get("question_text", raw.get("text", ""))).strip()
    if not q_text:
        errors.append("question_text must be non-empty")

    options = raw.get("options", [])
    if not isinstance(options, list):
        errors.append("options must be a list")
        options = []
    options = [str(o).strip() for o in options if o is not None]

    correct_answer = str(raw.get("correct_answer", "")).strip()

    type_config = QUESTION_TYPES.get(q_type, {})

    # Option-based validation
    if type_config.get("needs_options", False):
        min_opts = type_config.get("min_options", 2)
        if len(options) < min_opts:
            errors.append(f"{q_type} requires at least {min_opts} options")

    # specific validation per type
    if q_type in ("single_choice", "image_choice"):
        if correct_answer and options and correct_answer not in options:
            errors.append(f"correct_answer {correct_answer!r} not in options {options}")
    elif q_type == "true_false":
        if correct_answer and correct_answer.lower() not in (
            "true",
            "false",
            "对",
            "错",
            "正确",
            "错误",
        ):
            errors.append(f"true_false correct_answer must be true/false, got {correct_answer!r}")
    elif q_type == "fill_blank":
        acceptable = raw.get("acceptable_answers", [])
        if not correct_answer and not acceptable:
            errors.append("fill_blank requires correct_answer or acceptable_answers")

    if errors:
        return ValidationResult(valid=False, errors=errors)

    # Build the Question object
    acceptable = raw.get("acceptable_answers", [])
    if not isinstance(acceptable, list):
        acceptable = [str(acceptable)] if acceptable else []
    else:
        acceptable = [str(a) for a in acceptable]

    # Ensure correct_answer is in acceptable_answers
    if q_type == "fill_blank" and correct_answer and correct_answer not in acceptable:
        acceptable = [correct_answer] + acceptable

    question = Question(
        question_id=str(raw.get("question_id", _make_qid(q_text))),
        text=q_text,
        question_type=q_type,
        options=options,
        correct_answer=correct_answer,
        explanation=str(raw.get("explanation", "")),
        points=int(raw.get("points", 10)),
        hints=[str(h) for h in raw.get("hints", [])],
        acceptable_answers=acceptable,
        correct_answer_ids=[str(i) for i in raw.get("correct_answer_ids", [])],
        matching_pairs=[
            [str(p[0]), str(p[1])]
            for p in raw.get("matching_pairs", [])
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ],
        ordering_sequence=[str(s) for s in raw.get("ordering_sequence", [])],
        sub_questions=raw.get("sub_questions", [])
        if isinstance(raw.get("sub_questions"), list)
        else [],
    )

    return ValidationResult(valid=True, errors=[], question=question)


def check_answerability(q: Question) -> bool:
    """Check whether a question has enough information to be graded.

    A question is answerable when its type-specific correctness can be
    determined deterministically.

    Args:
        q: The question to check.

    Returns:
        ``True`` if the question can be graded.
    """
    if not q.text:
        return False

    if q.question_type in ("single_choice", "image_choice"):
        return bool(q.options) and q.correct_answer in q.options

    if q.question_type == "true_false":
        return q.correct_answer.lower() in ("true", "false", "对", "错", "正确", "错误")

    if q.question_type == "fill_blank":
        return bool(q.correct_answer) or bool(q.acceptable_answers)

    if q.question_type == "matching":
        return bool(q.matching_pairs)

    if q.question_type == "ordering":
        return bool(q.ordering_sequence)

    if q.question_type == "error_correction":
        return bool(q.correct_answer_ids) or bool(q.correct_answer)

    if q.question_type == "boss_comprehensive":
        return bool(q.sub_questions)

    return False


# ---------------------------------------------------------------------------
# ForgingService
# ---------------------------------------------------------------------------


class ForgingService:
    """Question generation service.

    Orchestrates LLM-based generation with template fallback, schema
    validation, and safety filtering.
    """

    def __init__(self, safety_filter: SafetyFilter | None = None) -> None:
        self._safety = safety_filter or SafetyFilter()

    async def generate_questions(
        self,
        chunks: Sequence[str],
        age_band: str,
        q_types: list[str] | None = None,
        llm_client: Any | None = None,
        count: int = 5,
    ) -> list[Question]:
        """Generate questions from source material.

        Args:
            chunks: Source text passages (corpus chunks).
            age_band: Target age band (``"7-9"``, ``"10-12"``, ``"13-15"``).
            q_types: Question types to generate.  Defaults to all types
                supported by the age band.
            llm_client: Optional LLM client with ``async complete(prompt)``.
                When ``None``, template questions are generated.
            count: Target number of questions.

        Returns:
            A list of validated, safety-checked :class:`Question` objects.
        """
        # Resolve question types for the age band
        supported = AGE_BAND_QUESTION_MATRIX.get(age_band, AGE_BAND_QUESTION_MATRIX["7-9"])
        if q_types is None:
            q_types = list(supported)
        # Filter to only supported types
        q_types = [qt for qt in q_types if qt in supported]
        if not q_types:
            q_types = list(supported)

        if llm_client is not None:
            questions = await self._generate_with_llm(chunks, age_band, q_types, llm_client, count)
        else:
            questions = self._generate_template(chunks, age_band, q_types, count)

        # Filter by safety + answerability
        safe_questions: list[Question] = []
        for q in questions:
            if not check_answerability(q):
                continue
            result = self._safety.check_question(q, age_band)
            if result.passed:
                safe_questions.append(q)

        return safe_questions

    async def _generate_with_llm(
        self,
        chunks: Sequence[str],
        age_band: str,
        q_types: list[str],
        llm_client: Any,
        count: int,
    ) -> list[Question]:
        """Generate questions via LLM with retry on validation failure."""
        corpus = "\n\n".join(chunks[:5])  # limit context
        max_opts = AGE_BAND_MAX_OPTIONS.get(age_band, 4)

        for attempt in range(MAX_REGENERATION_ATTEMPTS + 1):
            prompt = self._build_llm_prompt(corpus, age_band, q_types, count, max_opts, attempt)
            try:
                raw_response = await llm_client.complete(prompt)
                questions = self._parse_llm_response(raw_response)
            except Exception:
                questions = []

            if questions:
                return questions

        # All LLM attempts failed — fall back to template
        return self._generate_template(chunks, age_band, q_types, count)

    def _build_llm_prompt(
        self,
        corpus: str,
        age_band: str,
        q_types: list[str],
        count: int,
        max_opts: int,
        attempt: int,
    ) -> str:
        """Build the LLM prompt for question generation."""
        type_instructions = ", ".join(q_types)
        return (
            f"You are a question generator for children aged {age_band}.\n"
            f"Generate exactly {count} questions from the following material.\n"
            f"Question types to use: {type_instructions}.\n"
            f"Maximum {max_opts} options per question.\n"
            f"Each question MUST include: question_type, question_text, "
            f"options (for choice types), correct_answer, explanation.\n"
            f"For fill_blank include acceptable_answers.\n"
            f"For matching include matching_pairs (list of [left, right]).\n"
            f"For ordering include ordering_sequence (correct order).\n\n"
            f"Output ONLY a JSON object with this structure:\n"
            f'{{"questions": [{{"question_type": "...", "question_text": "...", '
            f'"options": [...], "correct_answer": "...", "explanation": "...", '
            f'"hints": [...]}}]}}\n\n'
            f"Material:\n{corpus[:2000]}\n"
        )

    def _parse_llm_response(self, raw: str) -> list[Question]:
        """Parse an LLM JSON response into validated Question objects."""
        # Extract JSON from response (handles ```json ... ``` fences)
        json_str = raw.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", json_str, re.DOTALL)
        if fence_match:
            json_str = fence_match.group(1).strip()

        # Find the outermost JSON object
        brace_start = json_str.find("{")
        brace_end = json_str.rfind("}")
        if brace_start != -1 and brace_end != -1:
            json_str = json_str[brace_start : brace_end + 1]

        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return []

        if not isinstance(data, dict):
            return []

        raw_questions = data.get("questions", [])
        if not isinstance(raw_questions, list):
            return []

        questions: list[Question] = []
        for rq in raw_questions:
            if not isinstance(rq, dict):
                continue
            result = validate_schema(rq)
            if result.valid and result.question is not None:
                questions.append(result.question)

        return questions

    def _generate_template(
        self,
        chunks: Sequence[str],
        age_band: str,
        q_types: list[str],
        count: int,
    ) -> list[Question]:
        """Generate simple template questions from corpus keywords.

        This is the no-LLM fallback.  It extracts keywords from the corpus
        and builds simple single-choice and true-false questions.
        """
        questions: list[Question] = []
        keywords = self._extract_keywords(chunks)

        if not keywords:
            # Ultimate fallback: generic questions
            keywords = [
                ("knowledge", "understanding", "wisdom", "learning"),
                ("science", "nature", "world", "discovery"),
                ("friendship", "kindness", "sharing", "caring"),
                ("courage", "brave", "strong", "bold"),
                ("creativity", "imagination", "art", "music"),
            ]

        max_opts = AGE_BAND_MAX_OPTIONS.get(age_band, 4)
        idx = 0
        generated = 0

        while generated < count and idx < len(keywords) * 3:
            kw = keywords[idx % len(keywords)]
            idx += 1

            if not isinstance(kw, tuple) or len(kw) < 2:
                continue

            correct_word = kw[0]
            distractors = list(kw[1:])
            # Ensure we have enough distractors
            while len(distractors) < max_opts - 1:
                other_kw = keywords[(idx + len(distractors)) % len(keywords)]
                if isinstance(other_kw, tuple) and len(other_kw) > 0:
                    distractors.append(other_kw[0])
                else:
                    distractors.append(f"option{len(distractors)}")

            # Decide question type
            q_type = q_types[generated % len(q_types)] if q_types else "single_choice"

            if q_type == "true_false":
                is_true = generated % 2 == 0
                q = Question(
                    question_id=f"tpl_tf_{generated}",
                    text=f"True or False: The word '{correct_word}' is related to what we just learned.",
                    question_type="true_false",
                    options=[],
                    correct_answer="true" if is_true else "false",
                    explanation=f"The answer is {'true' if is_true else 'false'}.",
                    hints=["Think about what you read in the passage."],
                    points=10,
                )
                questions.append(q)
                generated += 1
            elif q_type in ("single_choice", "image_choice"):
                opts = [correct_word] + distractors[: max_opts - 1]
                # Deduplicate while preserving order
                seen: set[str] = set()
                unique_opts: list[str] = []
                for o in opts:
                    if o not in seen:
                        seen.add(o)
                        unique_opts.append(o)

                if len(unique_opts) < 2:
                    continue

                q = Question(
                    question_id=f"tpl_sc_{generated}",
                    text="Which of the following appears in the reading material?",
                    question_type="single_choice",
                    options=unique_opts,
                    correct_answer=correct_word,
                    explanation=f"'{correct_word}' was mentioned in the passage.",
                    hints=["Look for the word that was in your reading."],
                    points=10,
                )
                questions.append(q)
                generated += 1
            else:
                # For other types, still make a single_choice
                opts = [correct_word] + distractors[: max_opts - 1]
                seen2: set[str] = set()
                unique_opts2: list[str] = []
                for o in opts:
                    if o not in seen2:
                        seen2.add(o)
                        unique_opts2.append(o)
                if len(unique_opts2) < 2:
                    continue
                q = Question(
                    question_id=f"tpl_misc_{generated}",
                    text="Which word was featured in the lesson?",
                    question_type="single_choice",
                    options=unique_opts2,
                    correct_answer=correct_word,
                    explanation=f"'{correct_word}' appeared in your reading material.",
                    hints=["Remember what you read."],
                    points=10,
                )
                questions.append(q)
                generated += 1

        return questions

    def _extract_keywords(self, chunks: Sequence[str]) -> list[tuple[str, ...]]:
        """Extract keywords from corpus chunks using simple frequency analysis.

        Returns a list of tuples where the first element is the keyword and
        remaining elements are distractor words.
        """
        import collections

        # Simple word frequency for both CJK and Latin text
        word_freq: collections.Counter[str] = collections.Counter()

        for chunk in chunks:
            # Extract Latin words (2+ chars)
            latin_words = re.findall(r"[a-zA-Z]{3,}", chunk.lower())
            word_freq.update(latin_words)

            # Extract CJK words (2+ consecutive CJK characters as "words")
            cjk_segments = re.findall(r"[\u4e00-\u9fff]{2,4}", chunk)
            word_freq.update(cjk_segments)

        # Filter common stop words
        stop_words = {
            "the",
            "and",
            "for",
            "are",
            "but",
            "not",
            "you",
            "all",
            "can",
            "her",
            "was",
            "one",
            "our",
            "out",
            "day",
            "had",
            "has",
            "his",
            "how",
            "its",
            "may",
            "new",
            "now",
            "old",
            "see",
            "way",
            "who",
            "did",
            "get",
            "let",
            "say",
            "she",
            "too",
            "use",
        }
        filtered = [
            (word, freq)
            for word, freq in word_freq.most_common(30)
            if word not in stop_words and freq >= 1
        ]

        if not filtered:
            return []

        # Build keyword tuples: each keyword + random-ish distractors
        all_words = [w for w, _ in filtered]
        keywords: list[tuple[str, ...]] = []
        for i, (word, _) in enumerate(filtered):
            distractors = [all_words[j] for j in range(len(all_words)) if j != i][:3]
            keywords.append((word, *distractors))

        return keywords


def _make_qid(text: str) -> str:
    """Generate a short deterministic ID from question text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "ForgingService",
    "QUESTION_TYPES",
    "AGE_BAND_QUESTION_MATRIX",
    "AGE_BAND_MAX_OPTIONS",
    "MAX_REGENERATION_ATTEMPTS",
    "ValidationResult",
    "validate_schema",
    "check_answerability",
]
