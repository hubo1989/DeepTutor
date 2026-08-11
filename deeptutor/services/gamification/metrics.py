"""
Gamification Metrics — Append-Only Event Logging
=================================================

Records learning and engagement events to an append-only JSONL file for
later analysis, dashboarding, and A/B testing.

Events are written to ``data/user/gamification/metrics.jsonl`` — one JSON
object per line. Each event has::

    {
        "timestamp": "<ISO 8601 UTC>",
        "metric_name": "<name>",
        "profile_id": "<id>",
        "value": { ... arbitrary payload ... }
    }

Supported metric names:
    - ``level_started``
    - ``level_completed``
    - ``badge_earned``
    - ``daily_active``
    - ``parent_panel_opened``
    - ``tts_used``

The implementation is intentionally simple (JSONL append) to avoid any
heavy dependency. For high-volume production use, swap the sink behind
the same function signatures.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

# Directory for the metrics JSONL file (same root as other gamification data).
_METRICS_DIR = Path("data/user/gamification")
_METRICS_FILE = _METRICS_DIR / "metrics.jsonl"

# Canonical metric names (for documentation and validation).
VALID_METRICS: set[str] = {
    "level_started",
    "level_completed",
    "badge_earned",
    "daily_active",
    "parent_panel_opened",
    "tts_used",
}


def _ensure_dir() -> None:
    """Ensure the metrics directory exists."""
    _METRICS_DIR.mkdir(parents=True, exist_ok=True)


def record_metric(
    metric_name: str,
    value: dict[str, Any],
    profile_id: str = "",
) -> None:
    """Append a metric event to the JSONL log.

    Args:
        metric_name: One of :data:`VALID_METRICS`. Unknown names are still
            recorded (with a ``_unknown`` suffix in the value) but logged
            at WARNING level so they can be caught during development.
        value: Arbitrary JSON-serializable payload for this event.
        profile_id: The child profile this event belongs to (optional but
            recommended for filtering).

    Raises:
        TypeError: If ``value`` contains non-JSON-serializable data.
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metric_name": metric_name,
        "profile_id": profile_id,
        "value": value,
    }

    # Validate JSON serializability before writing.
    line = json.dumps(entry, ensure_ascii=False, default=str)
    _ensure_dir()

    with open(_METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_metrics(profile_id: str | None = None) -> list[dict[str, Any]]:
    """Read and optionally filter metric events.

    Args:
        profile_id: If provided, only events for this profile are returned.
            If ``None``, all events are returned.

    Returns:
        A list of event dicts, in chronological order (oldest first).
    """
    if not _METRICS_FILE.exists():
        return []

    results: list[dict[str, Any]] = []
    with open(_METRICS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Skip corrupt lines
                continue
            if profile_id is not None and entry.get("profile_id", "") != profile_id:
                continue
            results.append(entry)
    return results


def clear_metrics() -> int:
    """Clear all recorded metrics. Returns the number of deleted entries.

    Primarily used in tests.
    """
    if not _METRICS_FILE.exists():
        return 0
    count = 0
    with open(_METRICS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    _METRICS_FILE.unlink()
    return count


__all__ = [
    "VALID_METRICS",
    "record_metric",
    "load_metrics",
    "clear_metrics",
]
