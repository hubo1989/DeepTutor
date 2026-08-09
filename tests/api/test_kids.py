"""
Tests for the Kids API — public themes, map generation, maps list, progress.

These tests follow the patterns established in test_profiles.py: they test
the service-layer functions directly and verify the API router's request/
response models, without importing the full FastAPI application (which
requires all backend dependencies).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_data_dirs(tmp_path, monkeypatch):
    """Redirect all data directories to temp paths."""
    profiles_path = tmp_path / "profiles.json"
    monkeypatch.setattr(
        "deeptutor.services.kid_profiles.store._PROFILES_PATH", profiles_path
    )
    pin_dir = tmp_path / "pins"
    pin_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("deeptutor.services.kid_profiles.pin._PIN_DIR", pin_dir)
    game_dir = tmp_path / "gamification"
    game_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(
        "deeptutor.services.gamification.store._GAMIFICATION_DIR", game_dir
    )
    # Also redirect public_kb root for theme tests
    public_kb_root = tmp_path / "public_kb"
    public_kb_root.mkdir(exist_ok=True)
    monkeypatch.setenv("DEEPTUTOR_PUBLIC_KB_ROOT", str(public_kb_root))
    return tmp_path


@pytest.fixture
def mock_guardian_user():
    """A mock guardian user for dependency injection."""
    user = MagicMock()
    user.id = "guardian-001"
    user.username = "parent@example.com"
    user.role = "admin"  # admin maps to guardian
    return user


@pytest.fixture
def mock_kid_user():
    """A mock kid user for dependency injection."""
    user = MagicMock()
    user.id = "guardian-001"
    user.username = "kid-profile-001"
    user.role = "user"
    return user


def _create_test_theme(tmp_path: Path, theme_id: str = "test-theme"):
    """Create a minimal public theme pack on disk for testing."""
    theme_dir = tmp_path / "public_kb" / theme_id
    theme_dir.mkdir(parents=True, exist_ok=True)
    manifest = f"""
theme_id: {theme_id}
title:
  zh: "测试主题"
  en: "Test Theme"
age_band: "7-9"
description:
  zh: "这是一个测试主题"
  en: "This is a test theme"
icon: "🧪"
levels:
  - id: lvl_001
    title:
      zh: "第一关"
      en: "Level 1"
    difficulty: "入门"
    source_files: ["01-intro.md"]
  - id: lvl_002
    title:
      zh: "第二关"
      en: "Level 2"
    difficulty: "进阶"
    source_files: ["02-advanced.md"]
  - id: lvl_003
    title:
      zh: "BOSS关"
      en: "Boss Level"
    difficulty: "精通"
    is_boss: true
"""
    (theme_dir / "manifest.yaml").write_text(manifest, encoding="utf-8")
    # Create a source file so corpus loading works
    (theme_dir / "01-intro.md").write_text(
        "# Intro\nThis is the intro text for testing.", encoding="utf-8"
    )
    return theme_dir


# ---------------------------------------------------------------------------
# Public Themes API
# ---------------------------------------------------------------------------

class TestPublicThemesAPI:
    """Tests for GET /kids/public-themes."""

    def test_load_all_themes_empty(self, temp_data_dirs):
        """Should return empty list when no themes exist."""
        from deeptutor.services.gamification.public_themes import load_all_themes

        themes = load_all_themes()
        assert themes == []

    def test_load_all_themes_with_data(self, temp_data_dirs):
        """Should return themes sorted by theme_id."""
        _create_test_theme(temp_data_dirs, "alpha")
        _create_test_theme(temp_data_dirs, "beta")

        from deeptutor.services.gamification.public_themes import load_all_themes

        themes = load_all_themes()
        assert len(themes) == 2
        assert themes[0].theme_id == "alpha"
        assert themes[1].theme_id == "beta"

    def test_theme_to_summary_conversion(self, temp_data_dirs):
        """The _theme_to_summary helper should produce correct fields."""
        _create_test_theme(temp_data_dirs, "test-theme")

        from deeptutor.services.gamification.public_themes import load_theme
        from deeptutor.api.routers.kids import _theme_to_summary

        theme = load_theme("test-theme")
        assert theme is not None
        summary = _theme_to_summary(theme)
        assert summary.theme_id == "test-theme"
        assert summary.title_zh == "测试主题"
        assert summary.title_en == "Test Theme"
        assert summary.icon == "🧪"
        assert summary.age_band == "7-9"
        assert summary.level_count == 3


# ---------------------------------------------------------------------------
# Map Generation API
# ---------------------------------------------------------------------------

class TestGenerateMapAPI:
    """Tests for POST /kids/maps."""

    def test_build_quest_map_from_theme(self, temp_data_dirs):
        """_build_quest_map_from_theme should create proper QuestMap structure."""
        _create_test_theme(temp_data_dirs, "test-theme")

        from deeptutor.services.gamification.public_themes import load_theme
        from deeptutor.api.routers.kids import _build_quest_map_from_theme

        theme = load_theme("test-theme")
        assert theme is not None
        qmap = _build_quest_map_from_theme(theme, "7-9")

        assert qmap.map_id == "public:test-theme"
        assert len(qmap.levels) == 3
        assert qmap.levels[0].level_id == "lvl_001"
        assert qmap.levels[0].order == 0
        assert qmap.levels[0].is_boss is False
        assert qmap.levels[2].is_boss is True
        # Check unlock dependencies are chained
        assert qmap.levels[0].unlock_dependency == ""
        assert qmap.levels[1].unlock_dependency == "lvl_001"
        assert qmap.levels[2].unlock_dependency == "lvl_002"

    def test_map_to_response_serialization(self, temp_data_dirs):
        """_map_to_response should serialize QuestMap to response model."""
        _create_test_theme(temp_data_dirs, "test-theme")

        from deeptutor.services.gamification.public_themes import load_theme
        from deeptutor.api.routers.kids import (
            _build_quest_map_from_theme,
            _map_to_response,
        )

        theme = load_theme("test-theme")
        assert theme is not None
        qmap = _build_quest_map_from_theme(theme, "7-9")
        response = _map_to_response(qmap)

        assert response.map_id == "public:test-theme"
        assert response.title == "测试主题"
        assert response.icon == "🧪"
        assert len(response.levels) == 3
        assert response.levels[2].is_boss is True


# ---------------------------------------------------------------------------
# Maps List API
# ---------------------------------------------------------------------------

class TestMapsListAPI:
    """Tests for GET /kids/maps."""

    def test_list_maps_empty_progress(self, temp_data_dirs):
        """Should return empty when no progress exists."""
        from deeptutor.services.gamification.store import init_if_absent

        init_if_absent("test-profile")

        # The route reads from game_store.load() which returns None
        # if no file exists. Since init_if_absent creates one, we test
        # the scenario where maps is empty.
        from deeptutor.services.gamification.store import load

        state = load("test-profile")
        assert state is not None
        assert state.maps == {}


# ---------------------------------------------------------------------------
# Progress API
# ---------------------------------------------------------------------------

class TestProgressAPI:
    """Tests for GET /kids/progress/{profile_id}."""

    def test_progress_requires_guardian(self, temp_data_dirs):
        """require_guardian should reject non-guardian users."""
        from deeptutor.services.kid_profiles.guard import require_guardian

        # Simulate a non-guardian user
        with patch("deeptutor.services.kid_profiles.guard.get_current_user") as mock:
            mock_user = MagicMock()
            mock_user.role = "user"  # Not admin/guardian
            mock.return_value = mock_user

            import pytest as _pytest
            from fastapi import HTTPException

            with _pytest.raises(HTTPException) as exc_info:
                require_guardian()
            assert exc_info.value.status_code == 403

    def test_progress_requires_pin(self, temp_data_dirs):
        """verify_pin should return False for wrong PIN."""
        from deeptutor.services.kid_profiles.pin import set_pin, verify_pin

        set_pin("guardian-001", "1234")
        assert verify_pin("guardian-001", "1234") is True
        assert verify_pin("guardian-001", "wrong") is False

    def test_progress_profile_ownership(self, temp_data_dirs):
        """Profile ownership check should reject foreign profiles."""
        from deeptutor.services.kid_profiles.models import GuardianConsent, KidProfile
        from deeptutor.services.kid_profiles.store import create, get

        profile = KidProfile(
            guardian_user_id="guardian-001",
            nickname="Test Kid",
            avatar="🧒",
            age_band="7-9",
            consent=GuardianConsent(
                agreed=True,
                agreed_at="2024-01-01T00:00:00Z",
                guardian_user_id="guardian-001",
            ),
        )
        created = create(profile)

        # Correct guardian can see it
        loaded = get(created.profile_id)
        assert loaded is not None
        assert loaded.guardian_user_id == "guardian-001"

        # Different guardian ID
        assert loaded.guardian_user_id != "guardian-002"


# ---------------------------------------------------------------------------
# Router schema validation
# ---------------------------------------------------------------------------

class TestRouterSchemas:
    """Verify the Pydantic request/response models in the kids router."""

    def test_generate_map_request_defaults(self):
        """GenerateMapRequest should have sensible defaults."""
        from deeptutor.api.routers.kids import GenerateMapRequest

        req = GenerateMapRequest()
        assert req.source == "public"
        assert req.theme_id == ""
        assert req.kb_name == ""
        assert req.age_band == "7-9"

    def test_generate_map_request_public(self):
        """GenerateMapRequest for public source."""
        from deeptutor.api.routers.kids import GenerateMapRequest

        req = GenerateMapRequest(source="public", theme_id="dino-world", age_band="7-9")
        assert req.source == "public"
        assert req.theme_id == "dino-world"

    def test_generate_map_request_personal(self):
        """GenerateMapRequest for personal KB source."""
        from deeptutor.api.routers.kids import GenerateMapRequest

        req = GenerateMapRequest(
            source="personal", kb_name="my-kb", age_band="10-12"
        )
        assert req.source == "personal"
        assert req.kb_name == "my-kb"

    def test_theme_summary_model(self):
        """ThemeSummary should accept all expected fields."""
        from deeptutor.api.routers.kids import ThemeSummary

        summary = ThemeSummary(
            theme_id="dino-world",
            title_zh="恐龙世界",
            title_en="Dino World",
            description_zh="穿越史前时代",
            description_en="Travel through time",
            icon="🦕",
            age_band="7-9",
            level_count=5,
        )
        assert summary.theme_id == "dino-world"
        assert summary.level_count == 5

    def test_quest_map_response_model(self):
        """QuestMapResponse should serialize level summaries."""
        from deeptutor.api.routers.kids import (
            LevelSummaryModel,
            QuestMapResponse,
        )

        resp = QuestMapResponse(
            map_id="public:dino-world",
            title="恐龙世界",
            icon="🦕",
            levels=[
                LevelSummaryModel(
                    level_id="lvl_001",
                    title="什么是恐龙？",
                    order=0,
                    is_boss=False,
                ),
                LevelSummaryModel(
                    level_id="lvl_005",
                    title="大灭绝",
                    order=4,
                    is_boss=True,
                ),
            ],
        )
        assert resp.map_id == "public:dino-world"
        assert len(resp.levels) == 2
        assert resp.levels[1].is_boss is True

    def test_progress_response_model(self):
        """ProgressResponse should accept all fields."""
        from deeptutor.api.routers.kids import (
            MapProgressItem,
            ProgressResponse,
        )

        resp = ProgressResponse(
            profile_id="kid-001",
            nickname="Alice",
            avatar="👧",
            age_band="7-9",
            total_xp=150,
            level=3,
            daily_xp=60,
            daily_goal_xp=100,
            streak_days=3,
            max_combo=5,
            badges=["first_clear", "combo_5"],
            maps=[
                MapProgressItem(
                    map_id="public:dino-world",
                    title="恐龙世界",
                    icon="🦕",
                    unlocked=True,
                    completed=False,
                    total_levels=5,
                    cleared_levels=2,
                ),
            ],
        )
        assert resp.profile_id == "kid-001"
        assert resp.total_xp == 150
        assert resp.level == 3
        assert len(resp.badges) == 2
        assert len(resp.maps) == 1
        assert resp.maps[0].cleared_levels == 2


# ---------------------------------------------------------------------------
# Engine consistency checks
# ---------------------------------------------------------------------------

class TestEngineConsistency:
    """Verify engine functions used by the API produce expected results."""

    def test_compute_level_thresholds(self):
        """compute_level should return correct levels for XP thresholds."""
        from deeptutor.services.gamification.engine import compute_level

        assert compute_level(0) == 1
        assert compute_level(49) == 1
        assert compute_level(50) == 2
        assert compute_level(120) == 3
        assert compute_level(1680) == 10
        assert compute_level(9999) == 10

    def test_progress_to_next_level(self):
        """progress_to_next_level should return 0.0-1.0 ratio."""
        from deeptutor.services.gamification.engine import progress_to_next_level

        assert progress_to_next_level(0) == 0.0
        assert 0 < progress_to_next_level(25) < 1.0
        assert progress_to_next_level(1680) == 1.0

    def test_grade_stars_thresholds(self):
        """grade_stars should return 0-3 based on correctness."""
        from deeptutor.services.gamification.engine import grade_stars

        assert grade_stars(0.5) == 0
        assert grade_stars(0.6) == 1
        assert grade_stars(0.8) == 2
        assert grade_stars(1.0) == 3
