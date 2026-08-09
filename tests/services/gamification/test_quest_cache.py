"""
Tests for the quest cache — content hashing, save/load round-trip, and
cache miss/hit behavior.
"""

from __future__ import annotations

import pytest

from deeptutor.capabilities.quest import quest_cache
from deeptutor.services.gamification.models import Level, Question, QuestMap


@pytest.fixture
def temp_quest_cache_dir(tmp_path, monkeypatch):
    """Redirect the quest cache directory to a temp path."""
    cache_dir = tmp_path / "quests"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(quest_cache, "_CACHE_DIR", cache_dir)
    return cache_dir


@pytest.fixture
def sample_questions():
    return [
        Question(
            question_id="q1",
            text="What is 2+2?",
            question_type="single_choice",
            options=["3", "4", "5"],
            correct_answer="4",
            explanation="2+2=4",
            points=10,
            hints=["Think about counting."],
        ),
        Question(
            question_id="q2",
            text="Is the sky blue?",
            question_type="true_false",
            correct_answer="true",
            explanation="Yes, the sky is blue during the day.",
            points=10,
        ),
    ]


@pytest.fixture
def sample_quest_map(sample_questions):
    level = Level(
        level_id="level_1",
        title="Level 1",
        description="First level",
        order=0,
        is_boss=False,
        questions=sample_questions,
    )
    return QuestMap(
        map_id="test_map",
        title="Test Quest",
        description="A test quest map",
        theme="math",
        icon="🧮",
        order=0,
        levels=[level],
    )


# ---------------------------------------------------------------------------
# compute_content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_deterministic_hash(self):
        chunks = ["Hello world", "Goodbye world"]
        q_types = ["single_choice", "true_false"]
        h1 = quest_cache.compute_content_hash(chunks, "7-9", q_types)
        h2 = quest_cache.compute_content_hash(chunks, "7-9", q_types)
        assert h1 == h2

    def test_different_chunks_different_hash(self):
        h1 = quest_cache.compute_content_hash(["chunk A"], "7-9", ["single_choice"])
        h2 = quest_cache.compute_content_hash(["chunk B"], "7-9", ["single_choice"])
        assert h1 != h2

    def test_different_age_band_different_hash(self):
        h1 = quest_cache.compute_content_hash(["same chunk"], "7-9", ["single_choice"])
        h2 = quest_cache.compute_content_hash(["same chunk"], "10-12", ["single_choice"])
        assert h1 != h2

    def test_q_types_order_independent(self):
        h1 = quest_cache.compute_content_hash(["chunk"], "7-9", ["true_false", "single_choice"])
        h2 = quest_cache.compute_content_hash(["chunk"], "7-9", ["single_choice", "true_false"])
        assert h1 == h2  # sorted internally

    def test_hash_length(self):
        h = quest_cache.compute_content_hash(["x"], "7-9", ["single_choice"])
        assert len(h) == 16  # truncated SHA-256


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------

class TestCacheRoundTrip:
    def test_save_then_load(self, temp_quest_cache_dir, sample_quest_map):
        quest_cache.save_cache("test_hash_123", sample_quest_map)
        loaded = quest_cache.get_cached("test_hash_123")
        assert loaded is not None
        assert loaded.map_id == "test_map"
        assert len(loaded.levels) == 1
        assert loaded.levels[0].level_id == "level_1"
        assert len(loaded.levels[0].questions) == 2
        assert loaded.levels[0].questions[0].question_id == "q1"
        assert loaded.levels[0].questions[0].correct_answer == "4"

    def test_load_nonexistent(self, temp_quest_cache_dir):
        result = quest_cache.get_cached("nonexistent_hash")
        assert result is None

    def test_preserves_question_fields(self, temp_quest_cache_dir, sample_quest_map):
        quest_cache.save_cache("field_test", sample_quest_map)
        loaded = quest_cache.get_cached("field_test")
        assert loaded is not None
        q = loaded.levels[0].questions[0]
        assert q.text == "What is 2+2?"
        assert q.question_type == "single_choice"
        assert q.options == ["3", "4", "5"]
        assert q.explanation == "2+2=4"
        assert q.hints == ["Think about counting."]
        assert q.points == 10

    def test_preserves_fill_blank_fields(self, temp_quest_cache_dir):
        q = Question(
            question_id="fb1",
            text="Fill: ___",
            question_type="fill_blank",
            correct_answer="Paris",
            acceptable_answers=["paris", "Paris"],
            points=15,
        )
        level = Level(level_id="lvl_fb", questions=[q])
        qm = QuestMap(map_id="fb_map", title="FB Test", levels=[level])
        quest_cache.save_cache("fb_hash", qm)
        loaded = quest_cache.get_cached("fb_hash")
        assert loaded is not None
        loaded_q = loaded.levels[0].questions[0]
        assert loaded_q.question_type == "fill_blank"
        assert loaded_q.acceptable_answers == ["paris", "Paris"]
        assert loaded_q.points == 15

    def test_overwrite_existing(self, temp_quest_cache_dir, sample_quest_map):
        quest_cache.save_cache("overwrite_test", sample_quest_map)
        # Modify and save again
        modified = QuestMap(map_id="modified", title="Modified", levels=[])
        quest_cache.save_cache("overwrite_test", modified)
        loaded = quest_cache.get_cached("overwrite_test")
        assert loaded is not None
        assert loaded.title == "Modified"


# ---------------------------------------------------------------------------
# Cache miss / hit
# ---------------------------------------------------------------------------

class TestCacheHitMiss:
    def test_cache_miss_returns_none(self, temp_quest_cache_dir):
        assert quest_cache.get_cached("miss_key") is None

    def test_cache_hit_after_save(self, temp_quest_cache_dir, sample_quest_map):
        quest_cache.save_cache("hit_key", sample_quest_map)
        result = quest_cache.get_cached("hit_key")
        assert result is not None
        assert result.map_id == "test_map"

    def test_cache_id_sanitization(self, temp_quest_cache_dir, sample_quest_map):
        """Unsafe characters in map_id should be sanitized."""
        quest_cache.save_cache("unsafe/../id!!", sample_quest_map)
        # Should not traverse — the sanitized key is "unsafeid"
        result = quest_cache.get_cached("unsafe/../id!!")
        assert result is not None
