"""
Tests for the gamification engine aggregation functions:
compute_weekly_report, identify_weak_topics, recommend_review.

These are pure-function tests — no IO, no LLM calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from deeptutor.services.gamification.engine import (
    compute_weekly_report,
    identify_weak_topics,
    recommend_review,
)
from deeptutor.services.gamification.models import (
    Level,
    LevelProgress,
    MapProgress,
    ProgressState,
    QuestMap,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_level_progress(
    *,
    stars: int = 0,
    best_correct_pct: float = 0.0,
    attempts: int = 0,
    cleared: bool = False,
    last_played_at: str = "",
) -> LevelProgress:
    """Create a LevelProgress with defaults."""
    return LevelProgress(
        stars=stars,
        best_correct_pct=best_correct_pct,
        attempts=attempts,
        cleared=cleared,
        last_played_at=last_played_at,
    )


def _recent_timestamp(days_ago: int = 0) -> str:
    """Return an ISO 8601 timestamp N days ago from now."""
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return ts.isoformat()


# ---------------------------------------------------------------------------
# compute_weekly_report
# ---------------------------------------------------------------------------

class TestComputeWeeklyReport:
    def test_empty_progress(self):
        """Empty ProgressState should produce zeroed report."""
        state = ProgressState(profile_id="test")
        report = compute_weekly_report(state)
        assert report["levels_completed"] == 0
        assert report["xp_earned"] == 0
        assert report["avg_accuracy"] == 0.0
        assert report["active_days"] == 0
        assert report["streak_days"] == 0

    def test_with_recent_activity(self):
        """Levels played recently should be counted."""
        ts = _recent_timestamp(days_ago=1)
        state = ProgressState(
            profile_id="test",
            streak_days=3,
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(
                            stars=2, best_correct_pct=0.8, attempts=1,
                            cleared=True, last_played_at=ts,
                        ),
                        "lvl_002": _make_level_progress(
                            stars=3, best_correct_pct=1.0, attempts=2,
                            cleared=True, last_played_at=ts,
                        ),
                    },
                ),
            },
        )
        report = compute_weekly_report(state)
        assert report["levels_completed"] == 2
        assert report["xp_earned"] > 0
        assert report["avg_accuracy"] == pytest.approx(0.9, abs=0.01)
        assert report["active_days"] >= 1
        assert report["streak_days"] == 3

    def test_old_activity_excluded(self):
        """Levels played >7 days ago should be excluded from default window."""
        old_ts = _recent_timestamp(days_ago=10)
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(
                            stars=2, best_correct_pct=0.7, attempts=1,
                            cleared=True, last_played_at=old_ts,
                        ),
                    },
                ),
            },
        )
        report = compute_weekly_report(state, days=7)
        assert report["levels_completed"] == 0
        assert report["active_days"] == 0

    def test_custom_days_window(self):
        """Custom days window should include older activity."""
        old_ts = _recent_timestamp(days_ago=15)
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(
                            stars=1, best_correct_pct=0.6, attempts=1,
                            cleared=True, last_played_at=old_ts,
                        ),
                    },
                ),
            },
        )
        report = compute_weekly_report(state, days=20)
        assert report["levels_completed"] == 1
        assert report["active_days"] == 1

    def test_unattempted_levels_excluded(self):
        """Levels with no attempts should not affect the report."""
        ts = _recent_timestamp(days_ago=0)
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(
                            stars=2, best_correct_pct=0.8, attempts=1,
                            cleared=True, last_played_at=ts,
                        ),
                        "lvl_002": _make_level_progress(
                            attempts=0, last_played_at="",
                        ),
                    },
                ),
            },
        )
        report = compute_weekly_report(state)
        assert report["levels_completed"] == 1
        assert report["avg_accuracy"] == pytest.approx(0.8, abs=0.01)


# ---------------------------------------------------------------------------
# identify_weak_topics
# ---------------------------------------------------------------------------

class TestIdentifyWeakTopics:
    def test_empty_progress(self):
        """No levels → no weak topics."""
        state = ProgressState(profile_id="test")
        result = identify_weak_topics(state)
        assert result == []

    def test_weak_level_identified(self):
        """Levels below threshold should be flagged."""
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(
                            best_correct_pct=0.4, attempts=2,
                        ),
                        "lvl_002": _make_level_progress(
                            best_correct_pct=0.9, attempts=1,
                        ),
                    },
                ),
            },
        )
        result = identify_weak_topics(state, threshold=0.6)
        assert len(result) == 1
        assert result[0]["level_id"] == "lvl_001"
        assert result[0]["map_id"] == "map1"
        assert result[0]["score_pct"] == 0.4

    def test_unattempted_excluded(self):
        """Unattempted levels (attempts=0) should be excluded."""
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(attempts=0, best_correct_pct=0.0),
                    },
                ),
            },
        )
        result = identify_weak_topics(state)
        assert result == []

    def test_custom_threshold(self):
        """Custom threshold should change which levels are flagged."""
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(best_correct_pct=0.7, attempts=1),
                    },
                ),
            },
        )
        # At threshold 0.6, 0.7 > 0.6 → not weak
        assert identify_weak_topics(state, threshold=0.6) == []
        # At threshold 0.8, 0.7 < 0.8 → weak
        result = identify_weak_topics(state, threshold=0.8)
        assert len(result) == 1

    def test_sorted_by_accuracy(self):
        """Results should be sorted by accuracy ascending (worst first)."""
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_a": _make_level_progress(best_correct_pct=0.5, attempts=1),
                        "lvl_b": _make_level_progress(best_correct_pct=0.2, attempts=1),
                        "lvl_c": _make_level_progress(best_correct_pct=0.4, attempts=1),
                    },
                ),
            },
        )
        result = identify_weak_topics(state, threshold=0.6)
        assert len(result) == 3
        assert result[0]["score_pct"] <= result[1]["score_pct"] <= result[2]["score_pct"]
        assert result[0]["level_id"] == "lvl_b"  # worst (0.2)

    def test_title_enrichment_from_maps(self):
        """When maps_data is provided, titles should be enriched."""
        state = ProgressState(
            profile_id="test",
            maps={
                "public:test": MapProgress(
                    map_id="public:test",
                    levels={
                        "lvl_001": _make_level_progress(best_correct_pct=0.3, attempts=1),
                    },
                ),
            },
        )
        qmaps = [
            QuestMap(
                map_id="public:test",
                title="Test Map",
                levels=[
                    Level(level_id="lvl_001", title="Introduction"),
                ],
            ),
        ]
        result = identify_weak_topics(state, maps_data=qmaps)
        assert len(result) == 1
        assert result[0]["title"] == "Introduction"

    def test_title_fallback_to_level_id(self):
        """Without maps_data, title should default to level_id."""
        state = ProgressState(
            profile_id="test",
            maps={
                "map1": MapProgress(
                    map_id="map1",
                    levels={
                        "lvl_001": _make_level_progress(best_correct_pct=0.3, attempts=1),
                    },
                ),
            },
        )
        result = identify_weak_topics(state)
        assert len(result) == 1
        assert result[0]["title"] == "lvl_001"


# ---------------------------------------------------------------------------
# recommend_review
# ---------------------------------------------------------------------------

class TestRecommendReview:
    def test_empty_weak_topics(self):
        """No weak topics → no recommendations."""
        state = ProgressState(profile_id="test")
        result = recommend_review(state, [])
        assert result == []

    def test_basic_recommendations(self):
        """Should return level IDs from weak topics."""
        state = ProgressState(profile_id="test")
        weak = [
            {"level_id": "lvl_001", "map_id": "map1", "title": "T1", "score_pct": 0.3},
            {"level_id": "lvl_002", "map_id": "map1", "title": "T2", "score_pct": 0.4},
        ]
        result = recommend_review(state, weak)
        assert result == ["lvl_001", "lvl_002"]

    def test_max_recommendations(self):
        """Should cap at max_recommendations."""
        state = ProgressState(profile_id="test")
        weak = [
            {"level_id": f"lvl_{i:03d}", "map_id": "map1", "title": "T", "score_pct": 0.1}
            for i in range(10)
        ]
        result = recommend_review(state, weak, max_recommendations=3)
        assert len(result) == 3
        assert result == ["lvl_000", "lvl_001", "lvl_002"]

    def test_deduplication(self):
        """Duplicate level IDs should be deduplicated."""
        state = ProgressState(profile_id="test")
        weak = [
            {"level_id": "lvl_001", "map_id": "map1", "title": "T", "score_pct": 0.3},
            {"level_id": "lvl_001", "map_id": "map1", "title": "T", "score_pct": 0.3},
        ]
        result = recommend_review(state, weak)
        assert result == ["lvl_001"]

    def test_preserves_order_from_weak_topics(self):
        """Recommendations should preserve the order from weak_topics (worst first)."""
        state = ProgressState(profile_id="test")
        weak = [
            {"level_id": "lvl_a", "map_id": "m", "title": "A", "score_pct": 0.2},
            {"level_id": "lvl_b", "map_id": "m", "title": "B", "score_pct": 0.4},
            {"level_id": "lvl_c", "map_id": "m", "title": "C", "score_pct": 0.5},
        ]
        result = recommend_review(state, weak)
        assert result == ["lvl_a", "lvl_b", "lvl_c"]
