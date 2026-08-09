"""
Quest Cache — Level/Question Caching
====================================

Caches generated quest maps (levels + questions) to avoid regenerating
identical questions when the same source material + age band + question
types are used.

Cache key: ``sha256(source_chunks + age_band + q_types)`` → stable ``map_id``.
Cache path: ``data/user/gamification/quests/<map_id>.json``
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from deeptutor.services.gamification.models import Level, Question, QuestMap
from deeptutor.services.file_io import atomic_write_json


_CACHE_DIR = Path("data/user/gamification/quests")


def _sanitize_cache_id(raw: str) -> str:
    """Sanitize a map_id for safe filesystem usage."""
    safe = "".join(c for c in raw if c.isalnum() or c in "-_")
    return safe or "default"


def _question_to_dict(q: Question) -> dict[str, Any]:
    """Serialize a Question to a JSON-safe dict."""
    return {
        "question_id": q.question_id,
        "text": q.text,
        "question_type": q.question_type,
        "options": list(q.options),
        "correct_answer": q.correct_answer,
        "explanation": q.explanation,
        "points": q.points,
        "hints": list(q.hints),
        "acceptable_answers": list(q.acceptable_answers),
        "correct_answer_ids": list(q.correct_answer_ids),
        "matching_pairs": [list(p) for p in q.matching_pairs],
        "ordering_sequence": list(q.ordering_sequence),
        "sub_questions": list(q.sub_questions),
    }


def _dict_to_question(d: dict[str, Any]) -> Question:
    """Deserialize a dict back to a Question."""
    return Question(
        question_id=str(d.get("question_id", "")),
        text=str(d.get("text", "")),
        question_type=str(d.get("question_type", "single_choice")),
        options=list(d.get("options", [])),
        correct_answer=str(d.get("correct_answer", "")),
        explanation=str(d.get("explanation", "")),
        points=int(d.get("points", 10)),
        hints=list(d.get("hints", [])),
        acceptable_answers=list(d.get("acceptable_answers", [])),
        correct_answer_ids=list(d.get("correct_answer_ids", [])),
        matching_pairs=[list(p) for p in d.get("matching_pairs", [])],
        ordering_sequence=list(d.get("ordering_sequence", [])),
        sub_questions=list(d.get("sub_questions", [])),
    )


def _level_to_dict(level: Level) -> dict[str, Any]:
    """Serialize a Level to a JSON-safe dict."""
    return {
        "level_id": level.level_id,
        "title": level.title,
        "description": level.description,
        "order": level.order,
        "is_boss": level.is_boss,
        "questions": [_question_to_dict(q) for q in level.questions],
        "unlock_dependency": level.unlock_dependency,
    }


def _dict_to_level(d: dict[str, Any]) -> Level:
    """Deserialize a dict back to a Level."""
    return Level(
        level_id=str(d.get("level_id", "")),
        title=str(d.get("title", "")),
        description=str(d.get("description", "")),
        order=int(d.get("order", 0)),
        is_boss=bool(d.get("is_boss", False)),
        questions=[_dict_to_question(qd) for qd in d.get("questions", [])],
        unlock_dependency=str(d.get("unlock_dependency", "")),
    )


def _quest_map_to_dict(qm: QuestMap) -> dict[str, Any]:
    """Serialize a QuestMap to a JSON-safe dict."""
    return {
        "map_id": qm.map_id,
        "title": qm.title,
        "description": qm.description,
        "theme": qm.theme,
        "icon": qm.icon,
        "order": qm.order,
        "levels": [_level_to_dict(lvl) for lvl in qm.levels],
        "unlock_dependency": qm.unlock_dependency,
    }


def _dict_to_quest_map(d: dict[str, Any]) -> QuestMap:
    """Deserialize a dict back to a QuestMap."""
    return QuestMap(
        map_id=str(d.get("map_id", "")),
        title=str(d.get("title", "")),
        description=str(d.get("description", "")),
        theme=str(d.get("theme", "")),
        icon=str(d.get("icon", "")),
        order=int(d.get("order", 0)),
        levels=[_dict_to_level(ld) for ld in d.get("levels", [])],
        unlock_dependency=str(d.get("unlock_dependency", "")),
    )


def compute_content_hash(
    source_chunks: Sequence[str],
    age_band: str,
    q_types: Sequence[str],
) -> str:
    """Compute a deterministic SHA-256 hash for the cache key.

    Args:
        source_chunks: The source text passages.
        age_band: Target age band.
        q_types: Question types used.

    Returns:
        A 16-character hex string (truncated SHA-256).
    """
    combined = "\n\n".join(source_chunks)
    raw = f"{combined}|{age_band}|{','.join(sorted(q_types))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_cached(map_id: str) -> QuestMap | None:
    """Retrieve a cached QuestMap by its map_id.

    Args:
        map_id: The cache key (content hash).

    Returns:
        The cached :class:`QuestMap`, or ``None`` if not found.
    """
    safe_id = _sanitize_cache_id(map_id)
    path = _CACHE_DIR / f"{safe_id}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_quest_map(raw)
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        return None


def save_cache(map_id: str, quest_map: QuestMap) -> None:
    """Persist a QuestMap to the cache.

    Args:
        map_id: The cache key (content hash).
        quest_map: The QuestMap to cache.
    """
    safe_id = _sanitize_cache_id(map_id)
    path = _CACHE_DIR / f"{safe_id}.json"
    payload = _quest_map_to_dict(quest_map)
    payload["_cache_key"] = map_id
    atomic_write_json(path, payload)


__all__ = [
    "compute_content_hash",
    "get_cached",
    "save_cache",
]
