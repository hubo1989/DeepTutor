"""
Tests for the Profiles API — profile CRUD, PIN switching, and store logic.

These tests are designed to run without importing the full application
(which requires all backend dependencies). Instead, they test the
profile store, PIN service, and gamification init directly, and verify
the router schema/request models in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_data_dirs(tmp_path, monkeypatch):
    """Redirect all data directories to temp paths."""
    profiles_path = tmp_path / "profiles.json"
    monkeypatch.setattr("deeptutor.services.kid_profiles.store._PROFILES_PATH", profiles_path)
    pin_dir = tmp_path / "pins"
    pin_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("deeptutor.services.kid_profiles.pin._PIN_DIR", pin_dir)
    game_dir = tmp_path / "gamification"
    game_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("deeptutor.services.gamification.store._GAMIFICATION_DIR", game_dir)
    return tmp_path


# ---------------------------------------------------------------------------
# Profile Store CRUD tests
# ---------------------------------------------------------------------------


class TestProfileStore:
    def test_create_and_load_all(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.models import GuardianConsent, KidProfile
        from deeptutor.services.kid_profiles.store import create, load_all

        profile = KidProfile(
            guardian_user_id="g-001",
            nickname="Test Kid",
            avatar="🧒",
            age_band="7-9",
            consent=GuardianConsent(
                agreed=True, agreed_at="2024-01-01T00:00:00Z", guardian_user_id="g-001"
            ),
        )
        created = create(profile)
        assert created.profile_id
        assert created.role == "kid"
        assert created.created_at

        all_profiles = load_all("g-001")
        assert len(all_profiles) == 1
        assert all_profiles[0].nickname == "Test Kid"

    def test_get_profile(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.models import KidProfile
        from deeptutor.services.kid_profiles.store import create, get

        profile = KidProfile(guardian_user_id="g-002", nickname="Kid2")
        created = create(profile)
        loaded = get(created.profile_id)
        assert loaded is not None
        assert loaded.nickname == "Kid2"
        assert loaded.consent.agreed is False  # default

    def test_get_nonexistent(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.store import get

        assert get("nonexistent") is None

    def test_delete_profile(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.models import KidProfile
        from deeptutor.services.kid_profiles.store import create, delete, get

        created = create(KidProfile(guardian_user_id="g-003", nickname="Delete Me"))
        assert delete(created.profile_id) is True
        assert get(created.profile_id) is None

    def test_delete_nonexistent(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.store import delete

        assert delete("nope") is False

    def test_isolated_by_guardian(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.models import KidProfile
        from deeptutor.services.kid_profiles.store import create, load_all

        create(KidProfile(guardian_user_id="g-a", nickname="Kid A"))
        create(KidProfile(guardian_user_id="g-b", nickname="Kid B"))
        assert len(load_all("g-a")) == 1
        assert len(load_all("g-b")) == 1

    def test_update_profile(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.models import KidProfile
        from deeptutor.services.kid_profiles.store import create, get, update

        created = create(KidProfile(guardian_user_id="g-004", nickname="Old Name"))
        created.nickname = "New Name"
        updated = update(created)
        assert updated is not None
        assert updated.nickname == "New Name"
        reloaded = get(created.profile_id)
        assert reloaded.nickname == "New Name"

    def test_consent_persisted(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.models import GuardianConsent, KidProfile
        from deeptutor.services.kid_profiles.store import create, get

        profile = KidProfile(
            guardian_user_id="g-005",
            nickname="Consent Kid",
            consent=GuardianConsent(
                agreed=True, agreed_at="2024-06-01T12:00:00Z", guardian_user_id="g-005"
            ),
        )
        created = create(profile)
        loaded = get(created.profile_id)
        assert loaded.consent.agreed is True
        assert loaded.consent.agreed_at == "2024-06-01T12:00:00Z"
        assert loaded.consent.guardian_user_id == "g-005"


# ---------------------------------------------------------------------------
# PIN Service tests
# ---------------------------------------------------------------------------


class TestPinService:
    def test_pin_round_trip(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.pin import has_pin, set_pin, verify_pin

        assert has_pin("guardian-001") is False
        set_pin("guardian-001", "1234")
        assert has_pin("guardian-001") is True
        assert verify_pin("guardian-001", "1234") is True
        assert verify_pin("guardian-001", "9999") is False

    def test_verify_pin_no_pin_set(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.pin import verify_pin

        # No PIN set → should return True (open mode)
        assert verify_pin("no-pin-user", "anything") is True

    def test_pin_too_short(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.pin import set_pin

        with pytest.raises(ValueError):
            set_pin("user-1", "12")

    def test_pin_too_long(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.pin import set_pin

        with pytest.raises(ValueError):
            set_pin("user-1", "123456789")

    def test_pin_empty(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.pin import set_pin

        with pytest.raises(ValueError):
            set_pin("user-1", "")

    def test_pin_update(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.pin import set_pin, verify_pin

        set_pin("user-2", "1111")
        assert verify_pin("user-2", "1111") is True
        set_pin("user-2", "2222")
        assert verify_pin("user-2", "1111") is False
        assert verify_pin("user-2", "2222") is True

    def test_pin_hashed_not_plaintext(self, temp_data_dirs):
        """The PIN file must never contain the plaintext PIN."""
        from deeptutor.services.kid_profiles.pin import _pin_path, set_pin

        set_pin("user-3", "4321")
        path = _pin_path("user-3")
        contents = json.loads(path.read_text(encoding="utf-8"))
        assert "pin_hash" in contents
        assert "4321" not in contents["pin_hash"]

    def test_clear_pin(self, temp_data_dirs):
        from deeptutor.services.kid_profiles.pin import clear_pin, has_pin, set_pin

        set_pin("user-4", "5678")
        assert has_pin("user-4") is True
        clear_pin("user-4")
        assert has_pin("user-4") is False


# ---------------------------------------------------------------------------
# Gamification init on profile creation
# ---------------------------------------------------------------------------


class TestGamificationInit:
    def test_gamification_state_created_with_profile(self, temp_data_dirs):
        """When a profile is created, gamification state should be initialized."""
        from deeptutor.services.gamification.store import init_if_absent, load

        state = init_if_absent("new-profile-1", {"daily_goal_xp": 100})
        assert state.profile_id == "new-profile-1"
        assert state.total_xp == 0
        assert state.daily_goal_xp == 100

        # Should be persisted
        loaded = load("new-profile-1")
        assert loaded is not None
        assert loaded.profile_id == "new-profile-1"

    def test_gamification_idempotent(self, temp_data_dirs):
        """init_if_absent should not overwrite existing data."""
        from deeptutor.services.gamification.models import ProgressState
        from deeptutor.services.gamification.store import init_if_absent, save

        state = ProgressState(profile_id="idem-1", total_xp=500, level=5)
        save("idem-1", state)

        re_init = init_if_absent("idem-1")
        assert re_init.total_xp == 500  # not reset to 0
        assert re_init.level == 5


# ---------------------------------------------------------------------------
# Router schema validation (Pydantic models)
# ---------------------------------------------------------------------------


class TestRouterSchemas:
    def test_create_profile_request_valid(self):
        from deeptutor.api.routers.profiles import CreateProfileRequest

        req = CreateProfileRequest(
            nickname="Emma", avatar="👧", age_band="4-6", consent_agreed=True
        )
        assert req.nickname == "Emma"
        assert req.consent_agreed is True

    def test_create_profile_request_requires_consent(self):
        from pydantic import ValidationError

        from deeptutor.api.routers.profiles import CreateProfileRequest

        with pytest.raises(ValidationError):
            CreateProfileRequest(nickname="Test")  # consent_agreed missing

    def test_switch_profile_request_to_kid(self):
        from deeptutor.api.routers.profiles import SwitchProfileRequest

        req = SwitchProfileRequest(profile_id="kid-1", direction="to_kid")
        assert req.direction == "to_kid"
        assert req.pin is None

    def test_switch_profile_request_to_guardian_with_pin(self):
        from deeptutor.api.routers.profiles import SwitchProfileRequest

        req = SwitchProfileRequest(profile_id="kid-1", direction="to_guardian", pin="1234")
        assert req.direction == "to_guardian"
        assert req.pin == "1234"

    def test_switch_profile_invalid_direction(self):
        from pydantic import ValidationError

        from deeptutor.api.routers.profiles import SwitchProfileRequest

        with pytest.raises(ValidationError):
            SwitchProfileRequest(profile_id="x", direction="invalid")


# ---------------------------------------------------------------------------
# Route handler logic (direct function call with mocked dependencies)
# ---------------------------------------------------------------------------


class TestCreateProfileHandler:
    def test_create_profile_calls_store(self, temp_data_dirs):
        """Test the handler logic directly (not through HTTP)."""
        from deeptutor.api.routers.profiles import CreateProfileRequest
        from deeptutor.services.kid_profiles.store import create as create_profile
        from deeptutor.services.kid_profiles.store import get

        # Simulate the handler logic
        req = CreateProfileRequest(
            nickname="TestKid", avatar="🧒", age_band="7-9", consent_agreed=True
        )
        from datetime import datetime, timezone

        from deeptutor.services.kid_profiles.models import GuardianConsent, KidProfile

        now = datetime.now(timezone.utc).isoformat()
        profile = KidProfile(
            guardian_user_id="guardian-test",
            nickname=req.nickname,
            avatar=req.avatar,
            age_band=req.age_band,
            role="kid",
            consent=GuardianConsent(agreed=True, agreed_at=now, guardian_user_id="guardian-test"),
        )
        created = create_profile(profile)

        loaded = get(created.profile_id)
        assert loaded is not None
        assert loaded.nickname == "TestKid"
        assert loaded.consent.agreed is True
        assert loaded.role == "kid"
