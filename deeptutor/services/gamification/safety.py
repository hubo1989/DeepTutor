"""
Safety Filter — Content Guardrail for Children
==============================================

Filters generated questions, explanations, and any user-facing text through
a deterministic wordlist and an optional LLM review hook.

Two-stage pipeline:
  1. **Wordlist** — fast, deterministic blacklist match against a curated
     list of violence, sexual, self-harm, drugs, and other child-inappropriate
     keywords (both Chinese and English).
  2. **LLM review** (optional) — a second-pass hook that sends the text to
     an LLM for nuanced semantic review.  When ``llm_client`` is ``None``
     the text passes through (wordlist result is final).

The filter is intentionally conservative: a flagged question is rejected
and the forging pipeline must regenerate it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FilterResult:
    """Outcome of a safety filter check.

    Attributes:
        passed: ``True`` if the text is safe for children.
        reason: Human-readable explanation when ``passed`` is ``False``.
        flagged_terms: List of terms that triggered the filter.
        sanitized_text: Text with flagged terms replaced by asterisks
            (useful for logging / debugging).
    """

    passed: bool = True
    reason: str = ""
    flagged_terms: list[str] = field(default_factory=list)
    sanitized_text: str = ""


# ---------------------------------------------------------------------------
# Wordlist
# ---------------------------------------------------------------------------

# Curated blacklist of child-inappropriate terms.
# Each entry is matched case-insensitively as a whole-word or substring.
WORDLIST: list[str] = [
    # --- Violence ---
    "kill", "murder", "blood", "gore", "torture", "stab", "shoot", "gun",
    "weapon", "bullet", "bomb", "explosive", "assault", "rape",
    "fight", "brawl", "attack", "strangle", "poison", "drown",
    "杀", "杀人", "杀戮", "谋杀", "流血", "酷刑", "刺杀", "枪杀", "炸弹",
    "爆炸", "武器", "子弹", "袭击", "强暴", "打架", "斗殴", "攻击",
    "勒死", "下毒", "淹死",
    # --- Sexual / Adult ---
    "sex", "porn", "nude", "naked", "erotic", "adult content",
    "色情", "裸体", "性爱", "成人内容", "淫秽",
    # --- Self-harm ---
    "suicide", "self-harm", "cut myself", "kill myself", "end my life",
    "ended his life", "ended her life", "took his life", "took her life",
    "took his own life", "took her own life",
    "自杀", "自残", "割腕", "轻生", "结束生命", "结束自己的生命",
    # --- Drugs ---
    "drug", "cocaine", "heroin", "marijuana", "weed", "meth",
    "吸毒", "毒品", "可卡因", "海洛因", "大麻", "冰毒",
    # --- Profanity (common) ---
    "fuck", "shit", "damn", "bitch", "asshole", "bastard",
    # --- Other inappropriate ---
    "gambling", "casino", "alcohol", "cigarette", "drunk",
    "赌博", "赌场", "酒精", "香烟", "酗酒",
]

# Pre-compile regex patterns for performance.
# Each term is matched as a case-insensitive substring.
_TERM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (term, re.compile(re.escape(term), re.IGNORECASE))
    for term in WORDLIST
]


def _mask(text: str, flagged: list[str]) -> str:
    """Replace flagged terms in *text* with asterisks of equal length."""
    sanitized = text
    for term in flagged:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        sanitized = pattern.sub("*" * len(term), sanitized)
    return sanitized


# ---------------------------------------------------------------------------
# LLM protocol (optional hook)
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """Minimal protocol the LLM review hook expects."""

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        """Return the model's completion text."""
        ...


# ---------------------------------------------------------------------------
# SafetyFilter
# ---------------------------------------------------------------------------

class SafetyFilter:
    """Two-stage safety filter for children's content.

    Stage 1 is always applied (wordlist).  Stage 2 (LLM review) runs
    only when an ``llm_client`` is supplied.
    """

    # Re-expose the wordlist for inspection / testing.
    WORDLIST: list[str] = WORDLIST

    @staticmethod
    def filter(text: str) -> FilterResult:
        """Run the deterministic wordlist filter on *text*.

        Args:
            text: The text to check.

        Returns:
            A :class:`FilterResult` with ``passed``, flagged terms, and
            sanitized text.
        """
        if not text:
            return FilterResult(passed=True, sanitized_text="")

        flagged: list[str] = []
        seen: set[str] = set()
        for term, pattern in _TERM_PATTERNS:
            if pattern.search(text) and term not in seen:
                flagged.append(term)
                seen.add(term)

        if flagged:
            return FilterResult(
                passed=False,
                reason=f"Flagged terms detected: {', '.join(flagged)}",
                flagged_terms=flagged,
                sanitized_text=_mask(text, flagged),
            )
        return FilterResult(passed=True, sanitized_text=text)

    @staticmethod
    async def llm_review(
        text: str,
        age_band: str,
        llm_client: Any | None = None,
    ) -> FilterResult:
        """Optional second-pass LLM review.

        When ``llm_client`` is ``None``, returns a pass-through result.

        Args:
            text: The text to review.
            age_band: Target age band (e.g. ``"7-9"``).
            llm_client: An object with an ``async complete(prompt) -> str``
                method.  If ``None``, the text passes.

        Returns:
            A :class:`FilterResult`.
        """
        # First stage: wordlist
        wordlist_result = SafetyFilter.filter(text)
        if not wordlist_result.passed:
            return wordlist_result

        if llm_client is None:
            return FilterResult(passed=True, sanitized_text=text)

        try:
            prompt = (
                f"You are a child safety reviewer for ages {age_band}.\n"
                f"Is the following text appropriate for children? "
                f"Reply ONLY 'SAFE' or 'UNSAFE: <reason>'.\n\n"
                f"Text: {text}"
            )
            response = await llm_client.complete(prompt)
            response_lower = response.strip().lower()
            if response_lower.startswith("unsafe"):
                reason = response.split(":", 1)[-1].strip() if ":" in response else "LLM flagged content"
                return FilterResult(
                    passed=False,
                    reason=f"LLM review: {reason}",
                    flagged_terms=[],
                    sanitized_text=text,
                )
        except Exception:
            # If the LLM call fails, fall back to wordlist-only result.
            pass

        return FilterResult(passed=True, sanitized_text=text)

    @staticmethod
    def check_question(q: Any, age_band: str) -> FilterResult:
        """Validate a Question object's text fields.

        Checks the question text, all options, and the correct answer.
        Returns the first failure, or a pass if all fields are clean.

        Args:
            q: A Question (duck-typed — must have ``text``, ``options``,
               and ``correct_answer`` attributes).
            age_band: Target age band.

        Returns:
            A :class:`FilterResult`.
        """
        # Check question text
        result = SafetyFilter.filter(q.text or "")
        if not result.passed:
            return result

        # Check options
        for opt in getattr(q, "options", []):
            result = SafetyFilter.filter(opt or "")
            if not result.passed:
                return result

        # Check correct answer
        result = SafetyFilter.filter(q.correct_answer or "")
        if not result.passed:
            return result

        # Check explanation if present
        explanation = getattr(q, "explanation", "")
        if explanation:
            result = SafetyFilter.filter(explanation)
            if not result.passed:
                return result

        return FilterResult(passed=True, sanitized_text=q.text or "")

    @staticmethod
    def check_explanation(text: str, age_band: str) -> FilterResult:
        """Validate an explanation or hint text.

        Args:
            text: The explanation text.
            age_band: Target age band.

        Returns:
            A :class:`FilterResult`.
        """
        return SafetyFilter.filter(text)


__all__ = [
    "FilterResult",
    "SafetyFilter",
    "WORDLIST",
    "LLMClient",
]
