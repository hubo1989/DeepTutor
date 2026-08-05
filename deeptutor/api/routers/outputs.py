"""Owner-scoped downloads for generated artifacts.

Unlike a process-wide ``StaticFiles`` mount, this router resolves the output
root after authentication has installed the request's current-user context.
The same relative artifact URL can therefore exist in multiple workspaces
without one user ever reaching another user's file.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from deeptutor.multi_user.context import get_current_user_or_none
from deeptutor.multi_user.paths import get_current_path_service

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


def _attachment_disposition(filename: str) -> str:
    fallback = filename.encode("ascii", errors="replace").decode("ascii")
    fallback = fallback.replace('"', "_").replace("\\", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.api_route("/{output_path:path}", methods=["GET", "HEAD"])
async def get_output(output_path: str):
    """Serve one whitelisted artifact from the authenticated owner's root."""
    # ``require_auth`` is installed on this router in api.main. Refuse to use
    # the legacy/default PathService if that invariant is ever broken, because
    # its default workspace is the administrator's.
    if get_current_user_or_none() is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    service = get_current_path_service()
    root = service.get_public_outputs_root().resolve()
    candidate = (root / Path(output_path)).resolve()
    if not candidate.is_file() or not service.is_public_output_path(candidate):
        raise HTTPException(status_code=404, detail="Output not found")

    headers = {
        "Cache-Control": "private, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": _RESTRICTIVE_CSP,
    }
    if candidate.suffix.lower() in _ACTIVE_CONTENT_SUFFIXES:
        headers["Content-Disposition"] = _attachment_disposition(candidate.name)
    return FileResponse(path=str(candidate), headers=headers)


__all__ = ["router"]
