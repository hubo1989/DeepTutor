"""
Request-scoped Kid Profile Context
===================================

Provides a ContextVar-based mechanism to track the active child profile
and current role within a single request lifecycle. This is set by the
``POST /api/v1/profiles/switch`` endpoint and consumed by downstream
services (gamification, safety filters, session limits).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class KidContext:
    """
    The active kid profile context for the current request.

    Attributes:
        profile_id: The active child's profile ID (empty when in guardian mode).
        role: The effective role for this request — "kid" or "guardian".
        guardian_user_id: The guardian account that owns this profile.
        age_band: The child's age band for content filtering.
    """

    profile_id: str = ""
    role: Literal["kid", "guardian"] = "guardian"
    guardian_user_id: str = ""
    age_band: str = ""


_current_kid_context: ContextVar[KidContext | None] = ContextVar(
    "deeptutor_kid_context", default=None
)


def set_kid_context(ctx: KidContext) -> Token[KidContext | None]:
    """Set the active kid context for the current request.

    Returns a token that should be passed to ``reset_kid_context``.
    """
    return _current_kid_context.set(ctx)


def reset_kid_context(token: Token[KidContext | None]) -> None:
    """Reset the kid context to its previous value."""
    _current_kid_context.reset(token)


def get_kid_context() -> KidContext | None:
    """Get the active kid context, or None if not in kid mode."""
    return _current_kid_context.get()


def get_kid_context_or_default() -> KidContext:
    """Get the active kid context, or a guardian-mode default if unset."""
    ctx = _current_kid_context.get()
    if ctx is None:
        return KidContext()
    return ctx


def is_kid_mode() -> bool:
    """Return True if the current request is in kid mode."""
    ctx = _current_kid_context.get()
    return ctx is not None and ctx.role == "kid"


__all__ = [
    "KidContext",
    "set_kid_context",
    "reset_kid_context",
    "get_kid_context",
    "get_kid_context_or_default",
    "is_kid_mode",
]
