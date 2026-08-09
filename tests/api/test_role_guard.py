"""
Tests for the role guard capability matrix.
Covers every capability × every role combination.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import HTTPException
import pytest

from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.kid_profiles.guard import (
    ROLE_CAPABILITIES,
    check_capability,
    require_guardian,
    require_kid_or_guardian,
)


def _make_user(role: str, user_id: str = "test-user") -> CurrentUser:
    """Create a CurrentUser with the given role."""
    scope = UserScope(kind="user", user_id=user_id, root="/tmp/test")
    return CurrentUser(id=user_id, username=f"{role}@test", role=role, scope=scope)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Capability matrix — every capability × every role
# ---------------------------------------------------------------------------

ALL_CAPABILITIES = [
    "kid_quest",
    "profile_manage",
    "kb_crud",
    "payment",
    "share",
    "parent_panel",
    "settings",
    "safety_filter",
    "session_limit",
]


class TestCapabilityMatrix:
    """Verify each capability returns the expected value for each role."""

    def test_guardian_kid_quest(self):
        user = _make_user("admin")  # admin → guardian
        assert check_capability(user, "kid_quest") is True

    def test_kid_kid_quest(self):
        user = _make_user("user")  # kid equivalent
        # Note: in this system, "user" role maps to "user" not "kid"
        # The guard maps admin→guardian, everything else stays as-is.
        # For kid role testing we need to handle this differently.
        # Since CurrentUser.role is Literal["admin","user"], kid isn't a valid
        # role in the existing auth system. We test the ROLE_CAPABILITIES
        # directly and check_capability with patched resolve.

    def test_role_capabilities_guardian_table(self):
        """Verify guardian capability values directly."""
        caps = ROLE_CAPABILITIES["guardian"]
        assert caps["kid_quest"] is True
        assert caps["profile_manage"] is True
        assert caps["kb_crud"] is True
        assert caps["payment"] is True
        assert caps["share"] is True
        assert caps["parent_panel"] is True
        assert caps["settings"] is True
        assert caps["safety_filter"] == "optional"
        assert caps["session_limit"] is None

    def test_role_capabilities_kid_table(self):
        """Verify kid capability values directly."""
        caps = ROLE_CAPABILITIES["kid"]
        assert caps["kid_quest"] is True
        assert caps["profile_manage"] is False
        assert caps["kb_crud"] is False
        assert caps["payment"] is False
        assert caps["share"] is False
        assert caps["parent_panel"] is False
        assert caps["settings"] is False
        assert caps["safety_filter"] == "always_on"
        assert caps["session_limit"] == 1200

    def test_all_capabilities_defined_for_guardian(self):
        """Every capability key must exist in the guardian matrix."""
        for cap in ALL_CAPABILITIES:
            assert cap in ROLE_CAPABILITIES["guardian"], f"Missing guardian capability: {cap}"

    def test_all_capabilities_defined_for_kid(self):
        """Every capability key must exist in the kid matrix."""
        for cap in ALL_CAPABILITIES:
            assert cap in ROLE_CAPABILITIES["kid"], f"Missing kid capability: {cap}"

    def test_guardian_can_manage_profiles(self):
        user = _make_user("admin")  # admin → guardian
        assert check_capability(user, "profile_manage") is True

    def test_kid_cannot_manage_profiles(self):
        """Simulate kid role by testing the capability table directly."""
        # kid role: profile_manage should be False
        assert ROLE_CAPABILITIES["kid"]["profile_manage"] is False

    def test_kid_cannot_access_payments(self):
        assert ROLE_CAPABILITIES["kid"]["payment"] is False

    def test_kid_cannot_share(self):
        assert ROLE_CAPABILITIES["kid"]["share"] is False

    def test_kid_cannot_access_parent_panel(self):
        assert ROLE_CAPABILITIES["kid"]["parent_panel"] is False

    def test_kid_cannot_change_settings(self):
        assert ROLE_CAPABILITIES["kid"]["settings"] is False

    def test_guardian_session_no_limit(self):
        assert ROLE_CAPABILITIES["guardian"]["session_limit"] is None

    def test_kid_session_limited_20min(self):
        assert ROLE_CAPABILITIES["kid"]["session_limit"] == 1200

    def test_guardian_safety_filter_optional(self):
        assert ROLE_CAPABILITIES["guardian"]["safety_filter"] == "optional"

    def test_kid_safety_filter_always_on(self):
        assert ROLE_CAPABILITIES["kid"]["safety_filter"] == "always_on"


# ---------------------------------------------------------------------------
# check_capability with mocked kid role
# ---------------------------------------------------------------------------

class TestCheckCapabilityKid:
    """Test check_capability with a mocked kid role (since CurrentUser.role
    is Literal['admin','user'], we mock _resolve_role)."""

    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="kid")
    def test_kid_can_quest(self, mock_role):
        user = _make_user("user")
        assert check_capability(user, "kid_quest") is True

    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="kid")
    def test_kid_cannot_crud_kb(self, mock_role):
        user = _make_user("user")
        assert check_capability(user, "kb_crud") is False

    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="kid")
    def test_kid_cannot_payment(self, mock_role):
        user = _make_user("user")
        assert check_capability(user, "payment") is False

    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="kid")
    def test_kid_cannot_share(self, mock_role):
        user = _make_user("user")
        assert check_capability(user, "share") is False


# ---------------------------------------------------------------------------
# require_guardian dependency
# ---------------------------------------------------------------------------

class TestRequireGuardian:
    @patch("deeptutor.services.kid_profiles.guard.get_current_user")
    def test_guardian_passes(self, mock_get):
        user = _make_user("admin")  # admin → guardian
        mock_get.return_value = user
        result = require_guardian()
        assert result.id == user.id

    @patch("deeptutor.services.kid_profiles.guard.get_current_user")
    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="kid")
    def test_kid_blocked(self, mock_role, mock_get):
        user = _make_user("user")
        mock_get.return_value = user
        with pytest.raises(HTTPException) as exc_info:
            require_guardian()
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# require_kid_or_guardian dependency
# ---------------------------------------------------------------------------

class TestRequireKidOrGuardian:
    @patch("deeptutor.services.kid_profiles.guard.get_current_user")
    def test_guardian_passes(self, mock_get):
        user = _make_user("admin")
        mock_get.return_value = user
        result = require_kid_or_guardian()
        assert result is not None

    @patch("deeptutor.services.kid_profiles.guard.get_current_user")
    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="kid")
    def test_kid_passes(self, mock_role, mock_get):
        user = _make_user("user")
        mock_get.return_value = user
        result = require_kid_or_guardian()
        assert result is not None


# ---------------------------------------------------------------------------
# Unknown role handling
# ---------------------------------------------------------------------------

class TestUnknownRole:
    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="unknown")
    def test_unknown_role_denied(self, mock_role):
        caps = ROLE_CAPABILITIES.get("unknown", {})
        # check_capability should return False for unknown roles
        user = _make_user("user")
        for cap in ALL_CAPABILITIES:
            assert check_capability(user, cap) is False

    @patch("deeptutor.services.kid_profiles.guard.get_current_user")
    @patch("deeptutor.services.kid_profiles.guard._resolve_role", return_value="unknown")
    def test_unknown_role_blocked_by_guard(self, mock_role, mock_get):
        user = _make_user("user")
        mock_get.return_value = user
        with pytest.raises(HTTPException) as exc_info:
            require_kid_or_guardian()
        assert exc_info.value.status_code == 403
