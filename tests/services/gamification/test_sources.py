"""
Tests for SourceResolver — public / personal knowledge source isolation.
"""

from __future__ import annotations

from deeptutor.services.gamification.sources import SourceHandle, SourceResolver


class TestResolvePublic:
    """resolve_public() produces correct handles for public themes."""

    def test_returns_correct_namespace_key(self) -> None:
        handle = SourceResolver.resolve_public("dino-world")
        assert handle.namespace_key == "public:dino-world"

    def test_is_public_true(self) -> None:
        handle = SourceResolver.resolve_public("dino-world")
        assert handle.is_public is True

    def test_source_type_is_public(self) -> None:
        handle = SourceResolver.resolve_public("dino-world")
        assert handle.source_type == "public"

    def test_identifier_is_theme_id(self) -> None:
        handle = SourceResolver.resolve_public("solar-system")
        assert handle.identifier == "solar-system"

    def test_strips_whitespace(self) -> None:
        handle = SourceResolver.resolve_public("  dino-world  ")
        assert handle.identifier == "dino-world"
        assert handle.namespace_key == "public:dino-world"


class TestResolvePersonal:
    """resolve_personal() produces correct handles for personal KBs."""

    def test_returns_correct_namespace_key(self) -> None:
        handle = SourceResolver.resolve_personal("profile_1", "my_kb")
        assert handle.namespace_key == "personal:profile_1:my_kb"

    def test_is_public_false(self) -> None:
        handle = SourceResolver.resolve_personal("profile_1", "my_kb")
        assert handle.is_public is False

    def test_source_type_is_personal(self) -> None:
        handle = SourceResolver.resolve_personal("profile_1", "my_kb")
        assert handle.source_type == "personal"

    def test_identifier_is_kb_name(self) -> None:
        handle = SourceResolver.resolve_personal("profile_1", "my_kb")
        assert handle.identifier == "my_kb"


class TestNamespaceIsolation:
    """public and personal namespaces must never collide."""

    def test_public_and_personal_keys_differ(self) -> None:
        public_handle = SourceResolver.resolve_public("dino-world")
        personal_handle = SourceResolver.resolve_personal("dino-world", "dino-world")
        assert public_handle.namespace_key != personal_handle.namespace_key

    def test_different_profiles_never_collide(self) -> None:
        h1 = SourceResolver.resolve_personal("profile_1", "my_kb")
        h2 = SourceResolver.resolve_personal("profile_2", "my_kb")
        assert h1.namespace_key != h2.namespace_key

    def test_different_themes_never_collide(self) -> None:
        h1 = SourceResolver.resolve_public("dino-world")
        h2 = SourceResolver.resolve_public("solar-system")
        assert h1.namespace_key != h2.namespace_key

    def test_different_kb_names_never_collide(self) -> None:
        h1 = SourceResolver.resolve_personal("profile_1", "kb_a")
        h2 = SourceResolver.resolve_personal("profile_1", "kb_b")
        assert h1.namespace_key != h2.namespace_key

    def test_cross_family_collision_impossible_by_prefix(self) -> None:
        """Even if a theme_id equals a kb_name and profile_id is empty-ish,
        the ``public:`` vs ``personal:`` prefix guarantees no collision."""
        public_handle = SourceResolver.resolve_public("same_name")
        # A personal handle with profile_id="x" and kb_name="same_name"
        personal_handle = SourceResolver.resolve_personal("x", "same_name")
        assert public_handle.namespace_key != personal_handle.namespace_key


class TestIsAvailable:
    """is_available() correctly reports source existence."""

    def test_public_existing_theme(self) -> None:
        assert SourceResolver.is_available("public", "dino-world") is True

    def test_public_existing_solar_system(self) -> None:
        assert SourceResolver.is_available("public", "solar-system") is True

    def test_public_existing_multiplication(self) -> None:
        assert SourceResolver.is_available("public", "multiplication-kingdom") is True

    def test_public_nonexistent(self) -> None:
        assert SourceResolver.is_available("public", "nonexistent") is False

    def test_public_empty_identifier(self) -> None:
        assert SourceResolver.is_available("public", "") is False

    def test_personal_returns_false_without_manager(self) -> None:
        """Personal availability requires a KB manager instance — the resolver
        only validates the public side."""
        assert SourceResolver.is_available("personal", "some_kb") is False


class TestSourceHandleIsPublic:
    """The is_public attribute is correct for both source types."""

    def test_public_handle_is_public(self) -> None:
        handle = SourceResolver.resolve_public("dino-world")
        assert handle.is_public is True

    def test_personal_handle_not_public(self) -> None:
        handle = SourceResolver.resolve_personal("p1", "kb1")
        assert handle.is_public is False


class TestRetrieve:
    """retrieve() is a stub returning an empty list (T03 fills it in)."""

    def test_returns_empty_list(self) -> None:
        handle = SourceResolver.resolve_public("dino-world")
        result = SourceResolver.retrieve(handle, "what are dinosaurs?")
        assert result == []

    def test_returns_empty_list_personal(self) -> None:
        handle = SourceResolver.resolve_personal("p1", "kb1")
        result = SourceResolver.retrieve(handle, "some query")
        assert result == []


class TestSourceHandleImmutability:
    """SourceHandle is frozen — fields cannot be reassigned."""

    def test_frozen_dataclass(self) -> None:
        handle = SourceResolver.resolve_public("dino-world")
        try:
            handle.identifier = "hacked"  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass  # Expected: frozen dataclass raises AttributeError
