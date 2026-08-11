"""
Public Theme Loader — discover and parse built-in theme packs.

A *theme pack* is a self-contained directory under ``data/public_kb/`` that
holds a curated learning topic for children::

    data/public_kb/<theme_id>/
    ├── manifest.yaml          # metadata + level definitions
    ├── 01-some-article.md     # source corpus
    └── ...

The loader scans the public-KB root once per call (the dataset is small — a
handful of themes, each with 5 markdown files — so caching is unnecessary).
Each ``manifest.yaml`` is parsed by PyYAML into a plain dict, then wrapped in
a :class:`PublicTheme` dataclass for ergonomic access.

The loader is deliberately decoupled from the gamification engine: it only
knows how to *find and parse* themes, nothing about XP, stars, or progression.
The engine and API layers consume :class:`PublicTheme` objects to build
:class:`QuestMap` instances and source material references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# The public-KB data root, resolved relative to the project root.  We walk up
# from this file (``deeptutor/services/gamification/public_themes.py``) three
# levels to reach the project root, then descend into ``data/public_kb``.
# This keeps the loader working regardless of the process's CWD.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PUBLIC_KB_ROOT = _PROJECT_ROOT / "data" / "public_kb"


@dataclass
class PublicTheme:
    """A parsed public theme pack.

    Attributes:
        theme_id: Stable identifier (e.g. ``"dino-world"``).
        title_i18n: Localised titles, e.g. ``{"zh": "恐龙世界", "en": "Dino World"}``.
        age_band: Target age range string (e.g. ``"7-9"``).
        description_i18n: Localised descriptions.
        icon: Emoji or icon string for UI display.
        levels: Raw level definitions from ``manifest.yaml`` (list of dicts).
    """

    theme_id: str = ""
    title_i18n: dict[str, str] = field(default_factory=dict)
    age_band: str = ""
    description_i18n: dict[str, str] = field(default_factory=dict)
    icon: str = ""
    levels: list[dict[str, Any]] = field(default_factory=list)

    @property
    def title(self) -> str:
        """Convenience accessor for the Chinese title (primary language)."""
        return self.title_i18n.get("zh", self.title_i18n.get("en", self.theme_id))

    @property
    def description(self) -> str:
        """Convenience accessor for the Chinese description."""
        return self.description_i18n.get("zh", self.description_i18n.get("en", ""))


def _get_public_kb_root() -> Path:
    """Return the public-KB data directory, allowing tests to override."""
    import os

    override = os.environ.get("DEEPTUTOR_PUBLIC_KB_ROOT", "")
    return Path(override) if override else _PUBLIC_KB_ROOT


def _parse_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Parse a ``manifest.yaml`` file, returning ``None`` on error."""
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _theme_from_manifest(data: dict[str, Any]) -> PublicTheme:
    """Build a :class:`PublicTheme` from a raw manifest dict."""
    title = data.get("title", {})
    if not isinstance(title, dict):
        title = {"zh": str(title), "en": str(title)}
    description = data.get("description", {})
    if not isinstance(description, dict):
        description = {"zh": str(description), "en": str(description)}
    levels = data.get("levels", [])
    if not isinstance(levels, list):
        levels = []
    return PublicTheme(
        theme_id=str(data.get("theme_id", "")),
        title_i18n={k: str(v) for k, v in title.items()},
        age_band=str(data.get("age_band", "")),
        description_i18n={k: str(v) for k, v in description.items()},
        icon=str(data.get("icon", "")),
        levels=levels,
    )


def load_theme(theme_id: str) -> PublicTheme | None:
    """Load a single public theme by its ``theme_id``.

    Args:
        theme_id: The theme identifier (directory name under ``public_kb``).

    Returns:
        A :class:`PublicTheme`, or ``None`` if the theme does not exist or its
        manifest is missing/invalid.
    """
    theme_id = (theme_id or "").strip()
    if not theme_id:
        return None
    manifest_path = _get_public_kb_root() / theme_id / "manifest.yaml"
    if not manifest_path.is_file():
        return None
    data = _parse_manifest(manifest_path)
    if data is None:
        return None
    return _theme_from_manifest(data)


def load_all_themes() -> list[PublicTheme]:
    """Load every public theme pack found under ``data/public_kb/``.

    Themes are returned sorted by ``theme_id`` for deterministic ordering.
    Directories without a valid ``manifest.yaml`` are silently skipped.
    """
    root = _get_public_kb_root()
    if not root.is_dir():
        return []
    themes: list[PublicTheme] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.yaml"
        if not manifest_path.is_file():
            continue
        data = _parse_manifest(manifest_path)
        if data is None:
            continue
        themes.append(_theme_from_manifest(data))
    return themes


def list_theme_ids() -> list[str]:
    """Return the sorted list of all available public theme IDs."""
    return [t.theme_id for t in load_all_themes() if t.theme_id]


__all__ = [
    "PublicTheme",
    "load_all_themes",
    "load_theme",
    "list_theme_ids",
]
