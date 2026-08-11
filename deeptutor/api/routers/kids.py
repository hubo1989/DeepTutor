"""
Kids API Router — gamified learning endpoints.

Exposes:
  - GET  /api/v1/kids/public-themes   — list public theme packs
  - POST /api/v1/kids/maps            — generate a challenge map (QuestMap)
  - GET  /api/v1/kids/maps            — list the active profile's maps
  - GET  /api/v1/kids/progress/{id}   — guardian-only progress panel data
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.kid_context import get_kid_context_or_default
from deeptutor.multi_user.models import CurrentUser
from deeptutor.services.gamification import engine as game_engine
from deeptutor.services.gamification import store as game_store
from deeptutor.services.gamification.models import QuestMap
from deeptutor.services.gamification.public_themes import (
    PublicTheme,
    load_all_themes,
    load_theme,
)
from deeptutor.services.kid_profiles.guard import require_guardian
from deeptutor.services.kid_profiles.pin import verify_pin
from deeptutor.services.kid_profiles.store import get as get_profile

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class ThemeSummary(BaseModel):
    """Lightweight public theme summary for card display."""

    theme_id: str
    title_zh: str = ""
    title_en: str = ""
    description_zh: str = ""
    description_en: str = ""
    icon: str = ""
    age_band: str = ""
    level_count: int = 0


class GenerateMapRequest(BaseModel):
    """Request body for POST /kids/maps."""

    source: str = Field(
        default="public",
        description='"public" for a theme pack, "personal" for a KB.',
    )
    theme_id: str = Field(
        default="",
        description='Public theme identifier (required when source="public").',
    )
    kb_name: str = Field(
        default="",
        description='Personal KB name (required when source="personal").',
    )
    age_band: str = Field(
        default="7-9",
        description='Target age band: "7-9", "10-12", or "13-15".',
    )


class LevelSummaryModel(BaseModel):
    """A level's summary within a generated map."""

    level_id: str
    title: str
    order: int = 0
    is_boss: bool = False


class QuestMapResponse(BaseModel):
    """Response for a generated QuestMap."""

    map_id: str
    title: str
    description: str = ""
    theme: str = ""
    icon: str = ""
    levels: list[LevelSummaryModel] = Field(default_factory=list)


class MapProgressItem(BaseModel):
    """One map's progress entry for the maps list."""

    map_id: str
    title: str = ""
    icon: str = ""
    unlocked: bool = False
    completed: bool = False
    total_levels: int = 0
    cleared_levels: int = 0


class WeeklyReportModel(BaseModel):
    """Weekly learning statistics."""

    levels_completed: int = 0
    xp_earned: int = 0
    avg_accuracy: float = 0.0
    active_days: int = 0
    streak_days: int = 0


class WeakTopicModel(BaseModel):
    """A weak topic identified from progress data."""

    level_id: str
    map_id: str
    title: str = ""
    score_pct: float = 0.0


class ProgressResponse(BaseModel):
    """Guardian-facing progress panel payload."""

    profile_id: str
    nickname: str = ""
    avatar: str = ""
    age_band: str = ""
    total_xp: int = 0
    level: int = 1
    daily_xp: int = 0
    daily_goal_xp: int = 100
    streak_days: int = 0
    max_combo: int = 0
    badges: list[str] = Field(default_factory=list)
    maps: list[MapProgressItem] = Field(default_factory=list)
    weekly_report: WeeklyReportModel = Field(default_factory=WeeklyReportModel)
    weak_topics: list[WeakTopicModel] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _theme_to_summary(theme: PublicTheme) -> ThemeSummary:
    """Convert a PublicTheme dataclass to a ThemeSummary response model."""
    return ThemeSummary(
        theme_id=theme.theme_id,
        title_zh=theme.title_i18n.get("zh", ""),
        title_en=theme.title_i18n.get("en", ""),
        description_zh=theme.description_i18n.get("zh", ""),
        description_en=theme.description_i18n.get("en", ""),
        icon=theme.icon,
        age_band=theme.age_band,
        level_count=len(theme.levels),
    )


def _build_quest_map_from_theme(theme: PublicTheme, age_band: str) -> QuestMap:
    """Build a QuestMap from a PublicTheme's manifest level definitions."""
    from deeptutor.services.gamification.models import Level

    levels: list[Level] = []
    for idx, lvl_def in enumerate(theme.levels):
        title_raw = lvl_def.get("title", {})
        if isinstance(title_raw, dict):
            title = title_raw.get("zh", title_raw.get("en", ""))
        else:
            title = str(title_raw)

        level_id = str(lvl_def.get("id", f"lvl_{idx + 1:03d}"))
        is_boss = bool(lvl_def.get("is_boss", False))

        levels.append(
            Level(
                level_id=level_id,
                title=title,
                order=idx,
                is_boss=is_boss,
                unlock_dependency=levels[-1].level_id if levels else "",
            )
        )

    map_id = f"public:{theme.theme_id}"
    return QuestMap(
        map_id=map_id,
        title=theme.title_i18n.get("zh", theme.theme_id),
        description=theme.description_i18n.get("zh", ""),
        theme=theme.theme_id,
        icon=theme.icon,
        order=0,
        levels=levels,
    )


def _map_to_response(qmap: QuestMap) -> QuestMapResponse:
    """Convert a QuestMap dataclass to a response model."""
    return QuestMapResponse(
        map_id=qmap.map_id,
        title=qmap.title,
        description=qmap.description,
        theme=qmap.theme,
        icon=qmap.icon,
        levels=[
            LevelSummaryModel(
                level_id=lv.level_id,
                title=lv.title,
                order=lv.order,
                is_boss=lv.is_boss,
            )
            for lv in qmap.levels
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/kids/public-themes", response_model=list[ThemeSummary])
async def list_public_themes(
    user: CurrentUser = Depends(get_current_user),
) -> list[ThemeSummary]:
    """Return the list of available public theme packs.

    Accessible by both kid and guardian roles.
    """
    themes = load_all_themes()
    return [_theme_to_summary(t) for t in themes]


@router.post("/kids/maps", response_model=QuestMapResponse)
async def generate_map(
    body: GenerateMapRequest,
    user: CurrentUser = Depends(get_current_user),
) -> QuestMapResponse:
    """Generate a challenge map (QuestMap) from a public theme or personal KB.

    For public themes, the map is built from the theme's manifest level
    definitions. For personal KBs, a placeholder map is created (full RAG
    generation happens during questing).
    """
    if body.source == "public":
        theme_id = body.theme_id.strip()
        if not theme_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="theme_id is required for public source.",
            )
        theme = load_theme(theme_id)
        if theme is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Theme '{theme_id}' not found.",
            )
        qmap = _build_quest_map_from_theme(theme, body.age_band)
        return _map_to_response(qmap)

    # Personal KB — build a basic map structure (levels forged on demand)
    if not body.kb_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="kb_name is required for personal source.",
        )
    from deeptutor.services.gamification.models import Level

    kid_ctx = get_kid_context_or_default()
    profile_id = kid_ctx.profile_id or user.id
    map_id = f"personal:{profile_id}:{body.kb_name}"
    levels = [
        Level(
            level_id=f"lvl_{i + 1:03d}",
            title=f"Level {i + 1}",
            order=i,
            is_boss=(i == 4),
            unlock_dependency=f"lvl_{i:03d}" if i > 0 else "",
        )
        for i in range(5)
    ]
    qmap = QuestMap(
        map_id=map_id,
        title=body.kb_name,
        description="",
        theme="personal",
        icon="📘",
        order=0,
        levels=levels,
    )
    return _map_to_response(qmap)


@router.get("/kids/maps", response_model=list[MapProgressItem])
async def list_maps(
    user: CurrentUser = Depends(get_current_user),
) -> list[MapProgressItem]:
    """List the active profile's maps with progress data."""
    kid_ctx = get_kid_context_or_default()
    profile_id = kid_ctx.profile_id or user.id

    state = game_store.load(profile_id)
    if state is None:
        return []

    items: list[MapProgressItem] = []
    for map_id, mp in state.maps.items():
        # Try to enrich with theme metadata for display
        title = map_id
        icon = "🗺️"
        if map_id.startswith("public:"):
            theme_id = map_id.split(":", 1)[1]
            theme = load_theme(theme_id)
            if theme:
                title = theme.title_i18n.get("zh", theme.theme_id)
                icon = theme.icon

        total = len(mp.levels)
        cleared = sum(1 for lp in mp.levels.values() if lp.cleared)
        items.append(
            MapProgressItem(
                map_id=map_id,
                title=title,
                icon=icon,
                unlocked=mp.unlocked,
                completed=mp.completed,
                total_levels=total,
                cleared_levels=cleared,
            )
        )
    return items


@router.get("/kids/progress/{profile_id}", response_model=ProgressResponse)
async def get_progress(
    profile_id: str,
    pin: str,
    guardian: CurrentUser = Depends(require_guardian),
) -> ProgressResponse:
    """Return detailed progress for a child profile (guardian-only).

    Requires guardian role and a valid PIN for COPPA-safe data access.
    """
    # Verify the profile belongs to this guardian
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found.",
        )
    if profile.guardian_user_id != guardian.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own profiles' progress.",
        )

    # Verify PIN
    if not verify_pin(guardian.id, pin):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect PIN.",
        )

    state = game_store.load(profile_id)
    if state is None:
        state = game_store.init_if_absent(profile_id)

    # Recompute level from total_xp for consistency
    computed_level = game_engine.compute_level(state.total_xp)

    # Build maps progress items
    maps_items: list[MapProgressItem] = []
    for map_id, mp in state.maps.items():
        title = map_id
        icon = "🗺️"
        if map_id.startswith("public:"):
            theme_id = map_id.split(":", 1)[1]
            theme = load_theme(theme_id)
            if theme:
                title = theme.title_i18n.get("zh", theme.theme_id)
                icon = theme.icon

        total = len(mp.levels)
        cleared = sum(1 for lp in mp.levels.values() if lp.cleared)
        maps_items.append(
            MapProgressItem(
                map_id=map_id,
                title=title,
                icon=icon,
                unlocked=mp.unlocked,
                completed=mp.completed,
                total_levels=total,
                cleared_levels=cleared,
            )
        )

    # Load QuestMap metadata for title enrichment (best-effort)
    quest_maps: list[QuestMap] = []
    for map_id in state.maps:
        if map_id.startswith("public:"):
            theme_id = map_id.split(":", 1)[1]
            theme = load_theme(theme_id)
            if theme:
                quest_maps.append(_build_quest_map_from_theme(theme, profile.age_band))

    # Compute weekly report, weak topics, and recommendations
    weekly = game_engine.compute_weekly_report(state, days=7)
    weak = game_engine.identify_weak_topics(state, maps_data=quest_maps or None, threshold=0.6)
    recs = game_engine.recommend_review(state, weak)

    return ProgressResponse(
        profile_id=profile_id,
        nickname=profile.nickname,
        avatar=profile.avatar,
        age_band=profile.age_band,
        total_xp=state.total_xp,
        level=computed_level,
        daily_xp=state.daily_xp,
        daily_goal_xp=state.daily_goal_xp,
        streak_days=state.streak_days,
        max_combo=state.max_combo,
        badges=list(state.badges),
        maps=maps_items,
        weekly_report=WeeklyReportModel(**weekly),
        weak_topics=[WeakTopicModel(**w) for w in weak],
        recommendations=recs,
    )


__all__ = ["router"]
