"""Tests for vision-model image captions on reading materials.

The vision client is stubbed so no network call is made; the store, the media
index round-trip and the refresh guard are exercised for real on a tmp store.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import random

from PIL import Image as PILImage
import pytest

from deeptutor.reading import captions as captions_module
from deeptutor.reading.models import ReadingError
from deeptutor.reading.store import ReadingStore, content_hash

_SOURCE_BYTES = b"deck contents"
_MATERIAL_ID = content_hash(_SOURCE_BYTES)


class _StubClient:
    """Minimal stand-in for the LLM facade used by the caption pass."""

    def __init__(self, *, vision: bool = True, fail: set[str] | None = None) -> None:
        self._vision = vision
        self._fail = fail or set()
        self.calls: list[str] = []

    def supports_multimodal_images(self) -> bool:
        return self._vision

    async def complete(
        self, prompt: str, system_prompt: str | None = None, **kwargs: object
    ) -> str:
        name = str(kwargs.get("image_filename") or "")
        self.calls.append(name)
        if name in self._fail:
            raise RuntimeError("vision call failed")
        return f"a chart labelled {name}"


@pytest.fixture(autouse=True)
def _fast_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(captions_module, "image_description_limits", lambda: (4, 5.0))


@pytest.fixture
def store(tmp_path: Path) -> ReadingStore:
    root = tmp_path / "reading"
    root.mkdir()
    store = ReadingStore(root)
    source = tmp_path / "deck.txt"
    source.write_bytes(_SOURCE_BYTES)
    store.ingest(source, filename="deck.txt")
    media_dir = root / _MATERIAL_ID / "media"
    media_dir.mkdir()
    rows = []
    for index in range(3):
        name = f"image-{index:02d}.png"
        (media_dir / name).write_bytes(b"png-bytes")
        rows.append({"name": name, "locator": 1, "mime": "image/png", "bytes": 9})
    (root / _MATERIAL_ID / "media.json").write_text(json.dumps(rows), encoding="utf-8")
    return store


@pytest.mark.asyncio
async def test_caption_material_media_writes_back(
    store: ReadingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubClient()
    monkeypatch.setattr(captions_module, "get_image_description_client", lambda: stub)

    written = await captions_module.caption_material_media(_MATERIAL_ID, store=store)

    assert written == 3
    assert len(stub.calls) == 3
    rows = store.media_items(_MATERIAL_ID)
    assert all(str(row.get("caption") or "") for row in rows)


@pytest.mark.asyncio
async def test_existing_captions_are_skipped(
    store: ReadingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubClient()
    monkeypatch.setattr(captions_module, "get_image_description_client", lambda: stub)

    assert await captions_module.caption_material_media(_MATERIAL_ID, store=store) == 3
    stub.calls.clear()

    again = await captions_module.caption_material_media(_MATERIAL_ID, store=store)

    assert again == 0
    assert stub.calls == []


@pytest.mark.asyncio
async def test_missing_captions_reuse_cache_but_force_refreshes(store, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from deeptutor.services.llm import image_caption_cache
    from deeptutor.services.llm.config import LLMConfig

    stub = _StubClient()
    stub.config = LLMConfig(model="vision-test", api_key="test-key")
    monkeypatch.setattr(captions_module, "get_image_description_client", lambda: stub)
    monkeypatch.setattr(
        image_caption_cache,
        "get_path_service",
        lambda: SimpleNamespace(get_parse_cache_root=lambda: tmp_path / "cache"),
    )
    rows = store.media_items(_MATERIAL_ID)
    index = tmp_path / "reading" / _MATERIAL_ID / "media.json"
    assert await captions_module.caption_material_media(_MATERIAL_ID, store=store) == 3
    index.write_text(json.dumps(rows))  # Retry ingest with the same source images.
    assert await captions_module.caption_material_media(_MATERIAL_ID, store=store) == 3
    assert len(stub.calls) == 3
    # The stub returns identical text, so the store reports no changed rows.
    assert await captions_module.caption_material_media(_MATERIAL_ID, store=store, force=True) == 0
    assert len(stub.calls) == 6


@pytest.mark.asyncio
async def test_single_failure_keeps_the_rest(
    store: ReadingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubClient(fail={"image-01.png"})
    monkeypatch.setattr(captions_module, "get_image_description_client", lambda: stub)

    written = await captions_module.caption_material_media(_MATERIAL_ID, store=store)

    assert written == 2
    captions = {row["name"]: row.get("caption") for row in store.media_items(_MATERIAL_ID)}
    assert captions["image-00.png"]
    assert captions["image-02.png"]
    assert not captions.get("image-01.png")


@pytest.mark.asyncio
async def test_text_only_client_makes_no_calls(
    store: ReadingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubClient(vision=False)
    monkeypatch.setattr(captions_module, "get_image_description_client", lambda: stub)

    written = await captions_module.caption_material_media(_MATERIAL_ID, store=store)

    assert written == 0
    assert stub.calls == []


def test_update_media_captions_ignores_unknown_names(store: ReadingStore) -> None:
    changed = store.update_media_captions(
        _MATERIAL_ID, {"missing.png": "ghost", "image-00.png": "hello"}
    )

    assert changed == 1
    captions = {row["name"]: row.get("caption") for row in store.media_items(_MATERIAL_ID)}
    assert captions["image-00.png"] == "hello"
    assert not captions.get("image-01.png")
    # An empty value never erases an existing caption nor counts as a change.
    assert store.update_media_captions(_MATERIAL_ID, {"image-01.png": ""}) == 0
