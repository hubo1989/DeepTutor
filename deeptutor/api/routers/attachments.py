"""HTTP endpoint for chat attachment downloads / previews.

The chat turn runtime persists every uploaded attachment to the
:class:`~deeptutor.services.storage.AttachmentStore` and records the public
URL on the message. The frontend preview drawer loads files via this
router, which only serves paths the store hands back — every component is
sanitised to defend against directory traversal.

URL shape::

    GET /api/attachments/{session_id}/{attachment_id}/{filename}

Authentication installs the current user's storage scope before this router
runs. Session and attachment identifiers are therefore resolved only inside
that owner's attachment root, even when another user chooses the same ids.
"""

from __future__ import annotations

import logging
import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from deeptutor.services.storage import (
    LocalDiskAttachmentStore,
    get_attachment_store,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_ACTIVE_CONTENT_SUFFIXES = {
    ".htm",
    ".html",
    ".mht",
    ".mhtml",
    ".svg",
    ".xhtml",
    ".xml",
}
_RESTRICTIVE_CSP = "sandbox; default-src 'none'"


def _content_disposition(filename: str, *, disposition: str = "inline") -> str:
    """Build a Content-Disposition header that survives non-ASCII filenames.

    HTTP/1.1 headers are latin-1, so dropping a Chinese / accented filename
    straight into ``filename="..."`` blows up with UnicodeEncodeError. RFC
    6266 / RFC 5987 cover this: emit ``filename*=UTF-8''<percent-encoded>``
    plus an ASCII fallback on ``filename=`` for legacy clients.
    """
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    # Quotes / backslashes break the simple-quoted-string form; collapse them.
    ascii_fallback = ascii_fallback.replace('"', "_").replace("\\", "_")
    encoded = quote(filename, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


@router.get("/{session_id}/{attachment_id}/{filename:path}")
async def get_attachment(
    session_id: str,
    attachment_id: str,
    filename: str,
):
    """Serve a previously uploaded chat attachment.

    Safe preview types use ``Content-Disposition: inline``. Active document
    formats such as HTML, SVG and XML are download-only and every response is
    protected by ``nosniff`` plus a sandboxed CSP.
    """
    store = get_attachment_store()
    if not isinstance(store, LocalDiskAttachmentStore):
        # Future remote backends should issue a redirect to the signed URL
        # here. Local-disk is the only backend today, so this branch just
        # guards against an unexpected configuration.
        raise HTTPException(status_code=501, detail="Attachment backend not servable")

    target = store.resolve_path(
        session_id=session_id,
        attachment_id=attachment_id,
        filename=filename,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    media_type, _ = mimetypes.guess_type(target.name)
    if not media_type:
        media_type = "application/octet-stream"

    # ``inline`` lets the browser preview the file when possible while still
    # honouring the suggested filename for the drawer's download action.
    disposition = "attachment" if target.suffix.lower() in _ACTIVE_CONTENT_SUFFIXES else "inline"
    headers = {
        "Content-Disposition": _content_disposition(target.name, disposition=disposition),
        # User-uploaded data; do not let intermediaries cache it.
        "Cache-Control": "private, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": _RESTRICTIVE_CSP,
    }
    return FileResponse(path=str(target), media_type=media_type, headers=headers)
