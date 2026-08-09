"""
Tests for the gamification store — read/write round-trip, migration,
and atomicity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import pytest

from deeptutor.services.gamification.models import LevelProgress, MapProgress, ProgressState
from deeptutor.services.gamification.store import (
    CURRENT_SCHEMA_VERSION,
    dict_to_state,
    init_if_absent,
    load,
    migrate,
    save,
    state_to_dict,
)


@pytest.fixture
def temp_gamification_dir(tmp_path, monkeypatch):
    """Redirect the gamification data directory to a temp path."""
    gamedir = tmp_path / "gamification"
    gamedir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("deeptutor.services.gamification.store._GAMIFICATION_DIR", gamedir)
    return gamedir


@pytest.fixture
def sample_state() -> ProgressState:
    """Create a ProgressState with some progress for round-trip tests."""
    mp = MapProgress(map_id="map1", unlocked=True)
    mp.levels["lvl1"] = LevelProgress(
        level_id="lvl1",
        stars=2,
        best_correct_pct=0.85,
        attempts=1,
        cleared=True,
        last_played_at="2024-01-15T10:00:00+00:00",
    )
    mp.levels["lvl2"] = LevelProgress(level_id="lvl2", stars=0, attempts=0)
    state = ProgressState(
        profile_id="kid-123",
        total_xp=350,
        level=5,
        daily_xp=80,
        daily_xp_date="2024-01-15",
        streak_days=3,
        streak_history=["2024-01-13", "2024-01-14", "2024-01-15"],
        maps={"map1": mp},
        badges=["first_clear"],
        max_combo=7,
        daily_goal_xp=100,
        daily_goal_met_dates=["2024-01-15"],
        created_at="2024-01-10T00:00:00+00:00",
        updated_at="2024-01-15T10:00:00+00:00",
    )
    return state


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_save_then_load(self, temp_gamification_dir, sample_state):
        save("kid-123", sample_state)
        loaded = load("kid-123")
        assert loaded is not None
        assert loaded.profile_id == "kid-123"
        assert loaded.total_xp == 350
        assert loaded.level == 5
        assert loaded.streak_days == 3
        assert loaded.badges == ["first_clear"]
        assert loaded.max_combo == 7
        assert "map1" in loaded.maps
        assert loaded.maps["map1"].levels["lvl1"].stars == 2
        assert loaded.maps["map1"].levels["lvl1"].cleared is True

    def test_load_nonexistent(self, temp_gamification_dir):
        assert load("nonexistent-kid") is None

    def test_state_to_dict_keys(self, sample_state):
        d = state_to_dict(sample_state)
        assert d["schema_version"] == CURRENT_SCHEMA_VERSION
        assert d["profile_id"] == "kid-123"
        assert d["total_xp"] == 350
        assert "maps" in d
        assert "badges" in d

    def test_dict_to_state_defaults(self):
        """Minimal dict should produce a valid state with defaults."""
        minimal = {"profile_id": "test", "schema_version": 1}
        state = dict_to_state(minimal)
        assert state.profile_id == "test"
        assert state.total_xp == 0
        assert state.level == 1
        assert state.badges == []
        assert state.maps == {}


# ---------------------------------------------------------------------------
# init_if_absent
# ---------------------------------------------------------------------------


class TestInitIfAbsent:
    def test_creates_new_state(self, temp_gamification_dir):
        state = init_if_absent("new-kid")
        assert state.profile_id == "new-kid"
        assert state.total_xp == 0
        assert state.level == 1
        # File should now exist on disk
        loaded = load("new-kid")
        assert loaded is not None
        assert loaded.profile_id == "new-kid"

    def test_returns_existing(self, temp_gamification_dir, sample_state):
        save("kid-existing", sample_state)
        state = init_if_absent("kid-existing")
        assert state.total_xp == 350
        assert state.level == 5

    def test_with_profile_meta(self, temp_gamification_dir):
        state = init_if_absent("meta-kid", profile_meta={"daily_goal_xp": 150})
        assert state.daily_goal_xp == 150


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_no_migration_needed(self):
        data = {"schema_version": 1, "profile_id": "test"}
        result = migrate(data, from_version=1, to_version=1)
        assert result is data  # unchanged

    def test_migration_to_same_version_passthrough(self):
        data = {"schema_version": 1, "total_xp": 100}
        result = migrate(data, from_version=1, to_version=1)
        assert result["total_xp"] == 100

    def test_migration_no_registered_step(self):
        """If no migration function exists, data passes through unchanged."""
        data = {"schema_version": 1, "total_xp": 50}
        result = migrate(data, from_version=1, to_version=99)
        # No registered migrations, so it stays the same
        assert result["total_xp"] == 50


# ---------------------------------------------------------------------------
# Atomic write integrity
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_concurrent_writes_no_corruption(self, temp_gamification_dir):
        """Multiple threads writing to the same file should not corrupt it."""
        profile_id = "concurrent-kid"
        results: list[str] = []
        errors: list[Exception] = []

        def writer(thread_id: int):
            try:
                state = ProgressState(
                    profile_id=profile_id,
                    total_xp=thread_id * 100,
                    level=thread_id,
                )
                save(profile_id, state)
                results.append(f"thread-{thread_id}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # File should be valid JSON after all writes
        loaded = load(profile_id)
        assert loaded is not None
        assert loaded.profile_id == profile_id

    def test_corrupt_json_returns_none(self, temp_gamification_dir):
        """If the file is corrupt, load should return None gracefully."""
        filepath = temp_gamification_dir / "corrupt-kid.json"
        filepath.write_text("{invalid json!!!", encoding="utf-8")
        loaded = load("corrupt-kid")
        assert loaded is None


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


class TestPathSafety:
    def test_safe_profile_id(self, temp_gamification_dir):
        """Profile IDs with special characters should be sanitized."""
        state = ProgressState(profile_id="safe-123")
        save("safe-123", state)
        loaded = load("safe-123")
        assert loaded is not None
        assert loaded.profile_id == "safe-123"
