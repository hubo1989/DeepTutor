"""Persistent storage for chat attachments.

The chat turn runtime writes the bytes of every uploaded attachment here
*before* the document extractor runs. Once persisted, the URL is recorded on
the message and the in-memory base64 is dropped (extractor still clears it
for office docs to save DB space). The frontend later fetches the original
file via the :mod:`deeptutor.api.routers.attachments` endpoint to render a
preview.

Design goals
------------

* **Local disk by default**: works in single-container Docker setups (the
  ``data/user`` volume is already mounted) and on plain Linux servers without
  any extra infrastructure.
* **Pluggable**: a thin :class:`AttachmentStore` protocol leaves room for an
  S3 / MinIO / GCS backend without touching call-sites.
* **Path-safe**: filenames coming over the WS are sanitised; resolved paths
  must remain inside the configured root.

The on-disk layout is::

    {root}/{session_id}/{attachment_id}_{filename}

The ``attachment_id`` prefix prevents collisions when the same filename is
uploaded twice in the same session.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import quote

from deeptutor.partners.helpers import safe_filename
from deeptutor.services.config import load_system_settings
from deeptutor.services.path_service import get_path_service

logger = logging.getLogger(__name__)


_DEFAULT_SUBPATH = ("workspace", "chat", "attachments")
# Public route prefix served by deeptutor.api.routers.attachments
_PUBLIC_URL_PREFIX = "/api/attachments"


def _coerce_filename(filename: str) -> str:
    """Reduce *filename* to a safe basename.

    * Strips any directory components (defends against ``../`` traversal).
    * Replaces filesystem-unsafe characters via the existing ``safe_filename``
      helper (already used by the matrix tutorbot uploads).
    * Falls back to ``"file"`` if the result is empty.
    """
    base = os.path.basename(filename or "")
    cleaned = safe_filename(base)
    return cleaned or "file"


@runtime_checkable
class AttachmentStore(Protocol):
    """Storage backend for chat attachments.

    Implementations must be safe to call from an asyncio context. The default
    :class:`LocalDiskAttachmentStore` uses ``run_in_executor`` to keep blocking
    disk I/O off the event loop.
    """

    async def put(
        self,
        *,
        session_id: str,
        attachment_id: str,
        filename: str,
        data: bytes,
        mime_type: str = "",
    ) -> str:
        """Persist *data* and return a public URL the frontend can fetch.

        The returned URL is relative to the API origin (e.g.
        ``"/api/attachments/<sid>/<aid>/<name>"``). Raising on failure is
        fine — callers log the error and proceed without ``url``.
        """

    async def delete_session(self, session_id: str) -> None:
        """Best-effort cleanup of all attachments for *session_id*."""

    async def delete_attachment(self, session_id: str, attachment_id: str) -> None:
        """Best-effort cleanup of a single attachment identified by *attachment_id*."""

    def resolve_path(self, *, session_id: str, attachment_id: str, filename: str) -> Path | None:
        """Return the absolute path on disk for an attachment, or ``None``
        if it does not exist or escapes the storage root.

        Used by the static router to serve files; remote-storage backends can
        return ``None`` and the router will then fall back to a redirect.
        """


class LocalDiskAttachmentStore:
    """Default :class:`AttachmentStore` backend writing to local disk.

    The root directory defaults to ``data/user/workspace/chat/attachments``
    under the project root (matching :class:`PathService`'s public outputs).
    Override via ``data/user/settings/system.json`` ``chat_attachment_dir``.
    """

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            root = _attachment_root()
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _stored_filename(self, attachment_id: str, filename: str) -> str:
        return f"{attachment_id}_{_coerce_filename(filename)}"

    def _session_dir(self, session_id: str) -> Path:
        sid = _coerce_filename(session_id)
        return (self._root / sid).resolve()

    def _safe_join(self, session_id: str, name: str) -> Path | None:
        """Join *name* under the session dir and confirm the result stays
        inside ``self._root``. Returns ``None`` if traversal is detected.
        """
        session_dir = self._session_dir(session_id)
        # Resolve the candidate even if it doesn't exist yet — prevents a
        # symlink-based attack that would point outside the root once created.
        candidate = (session_dir / name).resolve()
        try:
            candidate.relative_to(self._root.resolve())
        except ValueError:
            return None
        return candidate

    async def put(
        self,
        *,
        session_id: str,
        attachment_id: str,
        filename: str,
        data: bytes,
        mime_type: str = "",
    ) -> str:
        del mime_type  # not needed for local disk
        stored = self._stored_filename(attachment_id, filename)
        target = self._safe_join(session_id, stored)
        if target is None:
            raise ValueError(f"refusing to write attachment outside storage root: {stored!r}")

        from deeptutor.commercial.storage_limits import (
            atomic_write_bytes_with_storage_limits,
            commercial_owner_resource_lock,
        )

        async with commercial_owner_resource_lock():
            await asyncio.to_thread(
                atomic_write_bytes_with_storage_limits,
                target,
                data,
                enforce_upload_limit=True,
                additional_roots=(self._root,),
            )

        # The router uses the same _coerce_filename rules to look up the file,
        # so the public URL must use the sanitised pieces. Each path segment
        # is percent-encoded so spaces/Unicode/punctuation in filenames flow
        # through fetch / <iframe> consistently across browsers.
        sid = quote(_coerce_filename(session_id), safe="")
        aid = quote(attachment_id, safe="")
        name = quote(_coerce_filename(filename), safe="")
        return f"{_PUBLIC_URL_PREFIX}/{sid}/{aid}/{name}"

    async def delete_session(self, session_id: str) -> None:
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._rmtree_sync, session_dir)

    async def delete_attachment(self, session_id: str, attachment_id: str) -> None:
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_attachment_sync, session_dir, attachment_id)

    @staticmethod
    def _rmtree_sync(path: Path) -> None:
        import shutil

        try:
            shutil.rmtree(path)
        except OSError as exc:
            logger.warning("failed to clean up attachment dir %s: %s", path, exc)

    @staticmethod
    def _delete_attachment_sync(session_dir: Path, attachment_id: str) -> None:
        prefix = f"{attachment_id}_"
        for entry in session_dir.iterdir():
            if entry.name.startswith(prefix):
                try:
                    entry.unlink()
                except OSError as exc:
                    logger.warning("failed to delete attachment file %s: %s", entry, exc)
        try:
            if session_dir.exists() and not any(session_dir.iterdir()):
                session_dir.rmdir()
        except OSError as exc:
            logger.warning("failed to remove empty attachment dir %s: %s", session_dir, exc)

    def resolve_path(self, *, session_id: str, attachment_id: str, filename: str) -> Path | None:
        stored = self._stored_filename(attachment_id, filename)
        target = self._safe_join(session_id, stored)
        if target is None or not target.is_file():
            return None
        return target


_stores: dict[str, AttachmentStore] = {}


def get_attachment_store() -> AttachmentStore:
    """Return the process-wide :class:`AttachmentStore`.

    Today this is always a :class:`LocalDiskAttachmentStore`; future S3/MinIO
    backends can be selected here based on an env var.
    """
    root = _attachment_root()
    key = str(root)
    if key not in _stores:
        _stores[key] = LocalDiskAttachmentStore(root=root)
    return _stores[key]


def _attachment_root() -> Path:
    from deeptutor.multi_user.context import get_current_user_or_none
    from deeptutor.multi_user.paths import get_current_path_service

    user = get_current_user_or_none()
    override = str(load_system_settings().get("chat_attachment_dir") or "").strip()
    if override:
        configured_root = Path(override).expanduser().resolve()
        if user is None or user.scope.kind == "admin":
            return configured_root
        owner_root = external_attachment_root_for_owner(user.scope.user_id)
        if owner_root is None:  # pragma: no cover - override was just resolved
            raise RuntimeError("external attachment root could not be resolved")
        return owner_root

    # Preserve local/CLI behavior when there is no identity context. Inside
    # authenticated HTTP/WS work, resolve directly so an unexpected scoping
    # failure cannot silently fall back to the administrator's PathService.
    service = get_current_path_service() if user is not None else get_path_service()
    return service.get_user_root().joinpath(*_DEFAULT_SUBPATH).resolve()


def external_attachment_root_for_owner(owner_id: str) -> Path | None:
    """Resolve one owner's namespace under a deployment-level override.

    This context-free form lets account export/deletion cover attachments
    after the request's CurrentUser context has been torn down.  ``None``
    means attachments already live inside the ordinary per-user root.
    """

    override = str(load_system_settings().get("chat_attachment_dir") or "").strip()
    if not override:
        return None
    raw_owner = str(owner_id or "").strip()
    if not raw_owner:
        raise ValueError("attachment owner id is required")
    configured_root = Path(override).expanduser().resolve()
    safe_owner = _coerce_filename(raw_owner)[:48]
    digest = hashlib.sha256(raw_owner.encode("utf-8")).hexdigest()[:12]
    owner_root = (configured_root / "users" / f"{safe_owner}-{digest}").resolve()
    owner_root.relative_to(configured_root)
    return owner_root


def reset_attachment_store() -> None:
    """Reset the singleton — only meant for tests."""
    _stores.clear()
