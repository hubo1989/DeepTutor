"""
Source Resolver — public / personal knowledge source isolation.

The gamification layer distinguishes two source families:

* **public** — built-in, curated theme packs (``data/public_kb/<theme_id>/``).
  Every child profile reads the same content; the source is identified solely
  by ``theme_id``.
* **personal** — a child's own knowledge base, registered in the standard KB
  manager under ``data/knowledge_bases/<kb_name>/``. The source is identified
  by ``(profile_id, kb_name)``.

A :class:`SourceHandle` is the opaque token the rest of the gamification
pipeline carries around. Its ``namespace_key`` is constructed so that a public
key (``public:<theme_id>``) can **never** collide with a personal key
(``personal:<profile_id>:<kb_name>``) — the literal prefix guarantees isolation
even when a ``theme_id`` happens to equal a ``kb_name``.

Retrieval is intentionally stubbed here (returns ``[]``). The real RAG-backed
retrieval lands in T03 (the *forging* phase), where question generation needs
source material.
"""

from __future__ import annotations

from dataclasses import dataclass

from deeptutor.services.gamification.public_themes import list_theme_ids

# Namespace literal prefixes — they are deliberately distinct so a public
# key and a personal key can never be string-equal, regardless of the
# identifiers embedded after the colon.
_PUBLIC_PREFIX = "public"
_PERSONAL_PREFIX = "personal"


@dataclass(frozen=True)
class SourceHandle:
    """An opaque, immutable handle to a knowledge source.

    Attributes:
        source_type: ``"personal"`` or ``"public"``.
        identifier: For public sources this is the ``theme_id``; for personal
            sources it is the ``kb_name``.
        namespace_key: The fully-qualified, collision-free namespace string.
            ``public:<theme_id>`` or ``personal:<profile_id>:<kb_name>``.
        is_public: Convenience flag — ``True`` for public sources.
    """

    source_type: str  # "personal" | "public"
    identifier: str
    namespace_key: str
    is_public: bool


class SourceResolver:
    """Factory and inspector for :class:`SourceHandle` instances.

    All methods are static — the resolver is stateless. The class provides a
    clear API surface and groups related logic in one place.
    """

    @staticmethod
    def resolve_public(theme_id: str) -> SourceHandle:
        """Build a handle for a public theme pack.

        Args:
            theme_id: The theme identifier (e.g. ``"dino-world"``).

        Returns:
            A :class:`SourceHandle` with ``namespace_key="public:<theme_id>"``.
        """
        theme_id = (theme_id or "").strip()
        return SourceHandle(
            source_type=_PUBLIC_PREFIX,
            identifier=theme_id,
            namespace_key=f"{_PUBLIC_PREFIX}:{theme_id}",
            is_public=True,
        )

    @staticmethod
    def resolve_personal(profile_id: str, kb_name: str) -> SourceHandle:
        """Build a handle for a child's personal knowledge base.

        Args:
            profile_id: The child profile identifier.
            kb_name: The knowledge base name registered in the KB manager.

        Returns:
            A :class:`SourceHandle` with
            ``namespace_key="personal:<profile_id>:<kb_name>"``.
        """
        profile_id = (profile_id or "").strip()
        kb_name = (kb_name or "").strip()
        return SourceHandle(
            source_type=_PERSONAL_PREFIX,
            identifier=kb_name,
            namespace_key=f"{_PERSONAL_PREFIX}:{profile_id}:{kb_name}",
            is_public=False,
        )

    @staticmethod
    def is_available(source: str, identifier: str) -> bool:
        """Check whether a given source is currently available.

        For ``source="public"`` this consults the curated theme registry.
        Personal source availability is determined by the KB manager at call
        sites that have a ``base_dir`` — here we only validate the public side
        so the resolver stays free of IO dependencies.

        Args:
            source: ``"public"`` or ``"personal"``.
            identifier: ``theme_id`` (public) or ``kb_name`` (personal).

        Returns:
            ``True`` if the source exists / is registered.
        """
        source = (source or "").strip().lower()
        identifier = (identifier or "").strip()
        if not identifier:
            return False
        if source == _PUBLIC_PREFIX:
            return identifier in list_theme_ids()
        # Personal availability requires a KB manager instance (base_dir) —
        # callers that need that check should query the manager directly.
        return False

    @staticmethod
    def retrieve(handle: SourceHandle, query: str, top_n: int = 10) -> list[dict]:
        """Retrieve relevant passages from a knowledge source.

        .. note::
            This is a **stub** — it always returns an empty list. Real
            RAG-backed retrieval (embedding + vector search) is implemented in
            T03, the *forging* phase, where question generation consumes the
            results.

        Args:
            handle: The resolved :class:`SourceHandle`.
            query: The natural-language query.
            top_n: Maximum number of passages to return.

        Returns:
            An empty list (stub implementation).
        """
        # T03 will replace this with actual RAG retrieval.
        return []


__all__ = [
    "SourceHandle",
    "SourceResolver",
]
