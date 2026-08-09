"""
Gamification Store — JSON Persistence
=====================================

Persists ``ProgressState`` to ``data/user/gamification/<profile_id>.json``
using the shared ``atomic_write_json`` helper. Includes schema versioning
and a migration framework.

The store is the **only** module in the gamification subsystem that performs
IO. The engine functions remain pure by design.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from deeptutor.services.file_io import atomic_write_json

# Import models lazily to avoid circular imports (gamification/__init__.py
# imports from store.py which imports from models.py). The functions below
# import ProgressState et al. at call-time.
from deeptutor.services.gamification.models import (
    LevelProgress,
    MapProgress,
    ProgressState,
)

# Root directory for gamification data files.
_GAMIFICATION_DIR = Path("data/user/gamification")

# Current schema version. Bump when the JSON shape changes.
CURRENT_SCHEMA_VERSION: int = 1


def _profile_path(profile_id: str) -> Path:
    """Build the file path for a given profile's gamification data."""
    safe_id = _sanitize_profile_id(profile_id)
    return _GAMIFICATION_DIR / f"{safe_id}.json"


def _sanitize_profile_id(profile_id: str) -> str:
    """Sanitize the profile_id to prevent path traversal."""
    # Keep only alphanumeric, dash, underscore characters
    safe = "".join(c for c in profile_id if c.isalnum() or c in "-_")
    return safe or "unknown"


# ---------------------------------------------------------------------------
# Serialization (ProgressState ↔ dict)
# ---------------------------------------------------------------------------

def state_to_dict(state: ProgressState) -> dict[str, Any]:
    """Serialize a ProgressState to a JSON-safe dictionary."""
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "profile_id": state.profile_id,
        "total_xp": state.total_xp,
        "level": state.level,
        "daily_xp": state.daily_xp,
        "daily_xp_date": state.daily_xp_date,
        "streak_days": state.streak_days,
        "streak_history": list(state.streak_history),
        "maps": {
            map_id: {
                "map_id": mp.map_id,
                "unlocked": mp.unlocked,
                "completed": mp.completed,
                "levels": {
                    level_id: {
                        "level_id": lp.level_id,
                        "stars": lp.stars,
                        "best_correct_pct": lp.best_correct_pct,
                        "attempts": lp.attempts,
                        "cleared": lp.cleared,
                        "last_played_at": lp.last_played_at,
                    }
                    for level_id, lp in mp.levels.items()
                },
            }
            for map_id, mp in state.maps.items()
        },
        "badges": list(state.badges),
        "max_combo": state.max_combo,
        "daily_goal_xp": state.daily_goal_xp,
        "daily_goal_met_dates": list(state.daily_goal_met_dates),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def dict_to_state(data: dict[str, Any]) -> ProgressState:
    """Deserialize a dictionary back into a ProgressState."""
    maps: dict[str, MapProgress] = {}
    for map_id, mp_data in data.get("maps", {}).items():
        levels: dict[str, LevelProgress] = {}
        for level_id, lp_data in mp_data.get("levels", {}).items():
            levels[level_id] = LevelProgress(
                level_id=lp_data.get("level_id", level_id),
                stars=int(lp_data.get("stars", 0)),
                best_correct_pct=float(lp_data.get("best_correct_pct", 0.0)),
                attempts=int(lp_data.get("attempts", 0)),
                cleared=bool(lp_data.get("cleared", False)),
                last_played_at=lp_data.get("last_played_at", ""),
            )
        maps[map_id] = MapProgress(
            map_id=mp_data.get("map_id", map_id),
            levels=levels,
            unlocked=bool(mp_data.get("unlocked", False)),
            completed=bool(mp_data.get("completed", False)),
        )

    return ProgressState(
        profile_id=data.get("profile_id", ""),
        total_xp=int(data.get("total_xp", 0)),
        level=int(data.get("level", 1)),
        daily_xp=int(data.get("daily_xp", 0)),
        daily_xp_date=data.get("daily_xp_date", ""),
        streak_days=int(data.get("streak_days", 0)),
        streak_history=list(data.get("streak_history", [])),
        maps=maps,
        badges=list(data.get("badges", [])),
        max_combo=int(data.get("max_combo", 0)),
        daily_goal_xp=int(data.get("daily_goal_xp", 100)),
        daily_goal_met_dates=list(data.get("daily_goal_met_dates", [])),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        schema_version=CURRENT_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Migration framework
# ---------------------------------------------------------------------------

def migrate(data: dict[str, Any], from_version: int, to_version: int) -> dict[str, Any]:
    """
    Migrate a data dictionary from one schema version to another.

    Currently only version 1 exists, so this is a no-op pass-through.
    Future versions will add transformation steps here.

    Args:
        data: The raw dictionary loaded from disk.
        from_version: The schema version of the loaded data.
        to_version: The target schema version.

    Returns:
        The migrated dictionary.
    """
    current = data
    current_version = from_version
    while current_version < to_version:
        migration_fn = _MIGRATIONS.get(current_version)
        if migration_fn is None:
            break
        current = migration_fn(current)
        current["schema_version"] = current_version + 1
        current_version += 1
    return current


# Migration step registry: maps from_version → migration function.
# Add entries like {1: _migrate_v1_to_v2} when the schema evolves.
_MIGRATIONS: dict[int, Any] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def load(profile_id: str) -> ProgressState | None:
    """
    Load a ProgressState from disk for the given profile.

    Args:
        profile_id: The child profile identifier.

    Returns:
        The loaded ProgressState, or None if no data file exists.
    """
    path = _profile_path(profile_id)
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    data_version = int(raw.get("schema_version", 1))
    if data_version != CURRENT_SCHEMA_VERSION:
        raw = migrate(raw, data_version, CURRENT_SCHEMA_VERSION)

    return dict_to_state(raw)


def save(profile_id: str, state: ProgressState) -> None:
    """
    Atomically persist a ProgressState to disk.

    Args:
        profile_id: The child profile identifier.
        state: The progress state to save.
    """
    state.profile_id = profile_id
    state.updated_at = _now_iso()
    payload = state_to_dict(state)
    atomic_write_json(_profile_path(profile_id), payload)


def init_if_absent(
    profile_id: str,
    profile_meta: dict[str, Any] | None = None,
) -> ProgressState:
    """
    Initialize a ProgressState if none exists; return the existing one otherwise.

    Args:
        profile_id: The child profile identifier.
        profile_meta: Optional metadata to seed initial values (e.g., daily_goal_xp).

    Returns:
        The initialized or existing ProgressState.
    """
    existing = load(profile_id)
    if existing is not None:
        return existing

    now = _now_iso()
    meta = profile_meta or {}
    state = ProgressState(
        profile_id=profile_id,
        total_xp=0,
        level=1,
        daily_xp=0,
        daily_xp_date="",
        streak_days=0,
        streak_history=[],
        maps={},
        badges=[],
        max_combo=0,
        daily_goal_xp=int(meta.get("daily_goal_xp", 100)),
        daily_goal_met_dates=[],
        created_at=now,
        updated_at=now,
        schema_version=CURRENT_SCHEMA_VERSION,
    )
    save(profile_id, state)
    return state


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "state_to_dict",
    "dict_to_state",
    "migrate",
    "load",
    "save",
    "init_if_absent",
]
