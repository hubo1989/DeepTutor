"""
Tests for the gamification metrics module — JSONL append-only logging.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def temp_metrics_file(tmp_path, monkeypatch):
    """Redirect the metrics file to a temp path."""
    metrics_dir = tmp_path / "gamification"
    metrics_dir.mkdir(exist_ok=True)
    metrics_file = metrics_dir / "metrics.jsonl"

    monkeypatch.setattr(
        "deeptutor.services.gamification.metrics._METRICS_DIR", metrics_dir
    )
    monkeypatch.setattr(
        "deeptutor.services.gamification.metrics._METRICS_FILE", metrics_file
    )
    return metrics_file


class TestRecordMetric:
    def test_basic_record(self, temp_metrics_file):
        """record_metric should append a JSON line."""
        from deeptutor.services.gamification.metrics import record_metric

        record_metric("level_completed", {"level_id": "lvl_001"}, profile_id="kid-001")

        lines = temp_metrics_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["metric_name"] == "level_completed"
        assert entry["profile_id"] == "kid-001"
        assert entry["value"]["level_id"] == "lvl_001"
        assert "timestamp" in entry

    def test_multiple_appends(self, temp_metrics_file):
        """Multiple records should be appended sequentially."""
        from deeptutor.services.gamification.metrics import record_metric

        record_metric("level_started", {"level_id": "lvl_001"}, profile_id="kid-001")
        record_metric("level_completed", {"level_id": "lvl_001"}, profile_id="kid-001")
        record_metric("badge_earned", {"badge_id": "first_clear"}, profile_id="kid-001")

        lines = temp_metrics_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_non_serializable_handled(self, temp_metrics_file):
        """Non-serializable values should be converted via default=str."""
        from deeptutor.services.gamification.metrics import record_metric

        # A set is not JSON-serializable natively
        record_metric("test", {"data": {1, 2, 3}}, profile_id="test")

        lines = temp_metrics_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1  # Should not raise


class TestLoadMetrics:
    def test_empty_file(self, temp_metrics_file):
        """load_metrics on nonexistent file should return []."""
        from deeptutor.services.gamification.metrics import load_metrics

        assert load_metrics() == []

    def test_load_all(self, temp_metrics_file):
        """load_metrics without profile_id should return all entries."""
        from deeptutor.services.gamification.metrics import load_metrics, record_metric

        record_metric("a", {"v": 1}, profile_id="kid-001")
        record_metric("b", {"v": 2}, profile_id="kid-002")

        all_metrics = load_metrics()
        assert len(all_metrics) == 2

    def test_load_filtered(self, temp_metrics_file):
        """load_metrics with profile_id should filter."""
        from deeptutor.services.gamification.metrics import load_metrics, record_metric

        record_metric("a", {"v": 1}, profile_id="kid-001")
        record_metric("b", {"v": 2}, profile_id="kid-002")
        record_metric("c", {"v": 3}, profile_id="kid-001")

        kid1_metrics = load_metrics(profile_id="kid-001")
        assert len(kid1_metrics) == 2
        assert all(m["profile_id"] == "kid-001" for m in kid1_metrics)

    def test_corrupt_lines_skipped(self, temp_metrics_file):
        """Corrupt JSONL lines should be skipped gracefully."""
        from deeptutor.services.gamification.metrics import load_metrics, record_metric

        record_metric("valid", {"v": 1}, profile_id="kid-001")

        # Append a corrupt line
        with open(temp_metrics_file, "a", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write("\n")  # empty line

        result = load_metrics()
        assert len(result) == 1  # Only the valid entry


class TestClearMetrics:
    def test_clear_removes_file(self, temp_metrics_file):
        """clear_metrics should delete the file and return count."""
        from deeptutor.services.gamification.metrics import clear_metrics, record_metric

        record_metric("a", {}, profile_id="x")
        record_metric("b", {}, profile_id="y")

        count = clear_metrics()
        assert count == 2
        assert not temp_metrics_file.exists()

    def test_clear_when_no_file(self, temp_metrics_file):
        """clear_metrics on nonexistent file should return 0."""
        from deeptutor.services.gamification.metrics import clear_metrics

        count = clear_metrics()
        assert count == 0


class TestValidMetrics:
    def test_valid_metrics_set(self):
        """VALID_METRICS should contain expected names."""
        from deeptutor.services.gamification.metrics import VALID_METRICS

        expected = {
            "level_started",
            "level_completed",
            "badge_earned",
            "daily_active",
            "parent_panel_opened",
            "tts_used",
        }
        assert expected.issubset(VALID_METRICS)
