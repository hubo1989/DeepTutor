"""Tests for the connected-KB type discriminators.

The list (`GET /api/v1/knowledge`) and detail (`GET /{kb_name}`) endpoints now
derive ``read_only`` from :func:`is_connected_kb`, and the frontend mirrors the
same set in ``web/lib/knowledge-helpers.ts`` (``CONNECTED_KB_TYPES``). This test
locks the contract: every pointer/connected type reads as read-only, ordinary
indexed KBs do not.
"""

from __future__ import annotations

import pytest

from deeptutor.knowledge.kb_types import (
    CONNECTED_KB_TYPES,
    IMA_KB_TYPE,
    LINKED_KB_TYPE,
    LIGHTRAG_SERVER_KB_TYPE,
    OBSIDIAN_KB_TYPE,
    SUBAGENT_KB_TYPE,
    external_root_of,
    is_connected_kb,
)

ALL_CONNECTED = (
    OBSIDIAN_KB_TYPE,
    LINKED_KB_TYPE,
    SUBAGENT_KB_TYPE,
    LIGHTRAG_SERVER_KB_TYPE,
    IMA_KB_TYPE,
)


@pytest.mark.parametrize("kb_type", ALL_CONNECTED)
def test_connected_kb_types_are_read_only(kb_type: str) -> None:
    """Every pointer/connected type must read as connected (hence read-only)."""
    assert is_connected_kb({"type": kb_type})


def test_ima_is_in_connected_set() -> None:
    """Regression guard: IMA must stay classified as connected/read-only.

    IMA KBs are indexed by Tencent IMA; DeepTutor never builds or stores their
    index. Dropping IMA from the connected set would make the UI leak write
    actions (reindex/add documents) that error out server-side.
    """
    assert IMA_KB_TYPE in CONNECTED_KB_TYPES
    assert is_connected_kb({"type": IMA_KB_TYPE})


def test_ordinary_indexed_kb_is_not_connected() -> None:
    assert not is_connected_kb({"type": None})
    assert not is_connected_kb({"rag_provider": "llamaindex"})
    assert not is_connected_kb({})


def test_is_connected_kb_handles_non_dict() -> None:
    assert not is_connected_kb(None)
    assert not is_connected_kb("ima")


def test_external_root_prefers_external_path_then_vault_path() -> None:
    assert external_root_of({"external_path": "/data/linked"}) == "/data/linked"
    # Legacy obsidian entries store the vault location under ``vault_path``.
    assert external_root_of({"vault_path": "/vaults/notes"}) == "/vaults/notes"
    assert external_root_of({}) is None
