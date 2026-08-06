"""Stable, opaque deduplication keys for retryable external requests."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .errors import CommercialConflict


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for payload comparison and hashing."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise CommercialConflict("commercial payload must be valid JSON") from exc


def payload_sha256(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _opaque_key(prefix: str, *parts: str) -> str:
    normalized = "\x00".join(parts)
    digest = sha256(f"{prefix}:v1\x00{normalized}".encode("utf-8")).hexdigest()
    return f"{prefix}_v1_{digest}"


def request_dedupe_key(scope: str, subject_id: str, external_request_id: str) -> str:
    """Return a stable key scoped to an operation and commercial subject."""
    clean_scope = scope.strip().lower()
    clean_subject = subject_id.strip()
    clean_request = external_request_id.strip()
    if not clean_scope or not clean_subject or not clean_request:
        raise ValueError("scope, subject_id and external_request_id are required")
    return _opaque_key("req", clean_scope, clean_subject, clean_request)


def webhook_event_dedupe_key(provider: str, external_event_id: str) -> str:
    """Normalize provider identity while preserving its case-sensitive event id."""
    clean_provider = provider.strip().lower()
    clean_event_id = external_event_id.strip()
    if not clean_provider or not clean_event_id:
        raise ValueError("provider and external_event_id are required")
    return _opaque_key("evt", clean_provider, clean_event_id)
