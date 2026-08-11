"""
Tests for the public theme loader.
"""

from __future__ import annotations

from deeptutor.services.gamification.public_themes import (
    PublicTheme,
    list_theme_ids,
    load_all_themes,
    load_theme,
)


class TestLoadAllThemes:
    """load_all_themes() discovers every theme pack."""

    def test_returns_three_themes(self) -> None:
        themes = load_all_themes()
        assert len(themes) == 3

    def test_returns_correct_theme_ids(self) -> None:
        themes = load_all_themes()
        theme_ids = {t.theme_id for t in themes}
        assert theme_ids == {"dino-world", "solar-system", "multiplication-kingdom"}

    def test_themes_sorted_by_id(self) -> None:
        themes = load_all_themes()
        ids = [t.theme_id for t in themes]
        assert ids == sorted(ids)


class TestLoadThemeDinoWorld:
    """load_theme('dino-world') returns the correct theme."""

    def test_returns_theme(self) -> None:
        theme = load_theme("dino-world")
        assert theme is not None
        assert theme.theme_id == "dino-world"

    def test_title(self) -> None:
        theme = load_theme("dino-world")
        assert theme is not None
        assert theme.title_i18n["zh"] == "恐龙世界"
        assert theme.title_i18n["en"] == "Dino World"

    def test_age_band(self) -> None:
        theme = load_theme("dino-world")
        assert theme is not None
        assert theme.age_band == "7-9"

    def test_icon(self) -> None:
        theme = load_theme("dino-world")
        assert theme is not None
        assert theme.icon == "🦕"

    def test_has_five_levels(self) -> None:
        theme = load_theme("dino-world")
        assert theme is not None
        assert len(theme.levels) == 5

    def test_description(self) -> None:
        theme = load_theme("dino-world")
        assert theme is not None
        assert "恐龙" in theme.description_i18n["zh"]


class TestLoadThemeSolarSystem:
    """load_theme('solar-system') returns the correct theme."""

    def test_age_band_is_10_12(self) -> None:
        theme = load_theme("solar-system")
        assert theme is not None
        assert theme.age_band == "10-12"

    def test_title(self) -> None:
        theme = load_theme("solar-system")
        assert theme is not None
        assert theme.title_i18n["zh"] == "太阳系"

    def test_icon(self) -> None:
        theme = load_theme("solar-system")
        assert theme is not None
        assert theme.icon == "🪐"


class TestLoadThemeMultiplicationKingdom:
    """load_theme('multiplication-kingdom') returns the correct theme."""

    def test_has_five_levels(self) -> None:
        theme = load_theme("multiplication-kingdom")
        assert theme is not None
        assert len(theme.levels) == 5

    def test_last_level_is_boss(self) -> None:
        theme = load_theme("multiplication-kingdom")
        assert theme is not None
        last_level = theme.levels[-1]
        assert last_level.get("is_boss") is True

    def test_age_band(self) -> None:
        theme = load_theme("multiplication-kingdom")
        assert theme is not None
        assert theme.age_band == "7-9"


class TestLoadThemeNonexistent:
    """load_theme() returns None for unknown themes."""

    def test_nonexistent_returns_none(self) -> None:
        assert load_theme("nonexistent") is None

    def test_empty_string_returns_none(self) -> None:
        assert load_theme("") is None


class TestListThemeIds:
    """list_theme_ids() returns sorted theme IDs."""

    def test_returns_three_ids(self) -> None:
        ids = list_theme_ids()
        assert len(ids) == 3

    def test_contains_all_themes(self) -> None:
        ids = set(list_theme_ids())
        assert ids == {"dino-world", "solar-system", "multiplication-kingdom"}

    def test_sorted(self) -> None:
        ids = list_theme_ids()
        assert ids == sorted(ids)
