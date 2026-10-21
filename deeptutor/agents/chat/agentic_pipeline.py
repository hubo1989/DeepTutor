"""Chat's binding of the agent loop.

Chat is the loop the base host describes, so this is only a name: the
exploring-loop protocol, the tool surface and the prompt pack in
:mod:`deeptutor.agents.chat.prompts` are all the defaults declared on
:class:`~deeptutor.agents.loop.pipeline.AgenticLoopPipeline`.

The deep modes that run chat's own protocol (solve, ask_questions, reading,
watching, course study, visualize) construct this class directly, overriding
only budgets and stream namespace. A mode whose protocol differs from chat's
subclasses the host instead — see
:class:`deeptutor.capabilities.mastery.pipeline.MasteryLoopPipeline`.

Module-level symbols are re-exported here because they are patched by name in
tests and read by :mod:`deeptutor.agents.chat.capability`.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path
from typing import Any

import logging

from deeptutor.agents.loop.pipeline import (
    KB_SEED_CHARS_PER_KB,
    KB_SEED_MAX_KBS,
    LOOP_EXCLUDED_TOOLS,
    LOOP_OPTIONAL_TOOLS,
    AgenticLoopPipeline,
    _DispatchOutcome,
    _read_int,
)
from deeptutor.core.context import Attachment, UnifiedContext

logger = logging.getLogger(__name__)

#: Per-turn caps for retrieved RAG page images (fork feature).
RAG_IMAGE_MAX_ATTACHMENTS = 6
RAG_IMAGE_MAX_BYTES = 8 * 1024 * 1024

#: Chat's optional-tool whitelist. Every loop shares it: a mode never silently
#: removes a tool the user turned on for themselves.
CHAT_OPTIONAL_TOOLS = LOOP_OPTIONAL_TOOLS
CHAT_EXCLUDED_TOOLS = LOOP_EXCLUDED_TOOLS


class AgenticChatPipeline(AgenticLoopPipeline):
    """Run a chat turn as one exploring agent loop."""

    def _attach_rag_images(
        self,
        context: UnifiedContext,
        sources: list[dict[str, Any]] | None,
    ) -> list[Attachment]:
        """Load images from retrieved KB pages as Vision LLM attachments.

        Image bytes stay out of the vector index and RAG text. They are read
        only after retrieval, constrained to this runtime's data directory,
        capped per turn, and deduplicated across seed/tool retrievals.
        """
        if not sources:
            return []

        known_paths = {
            str(path)
            for path in (context.metadata.get("_rag_image_paths") or [])
            if str(path).strip()
        }
        runtime_root = None
        try:
            from deeptutor.runtime.home import get_runtime_data_root

            runtime_root = get_runtime_data_root().resolve()
        except Exception:
            logger.warning("could not resolve runtime data root; skipping RAG images")
            return []

        added: list[Attachment] = []
        candidate_paths: list[str] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            paths = source.get("image_paths")
            if not isinstance(paths, list):
                continue
            for raw_path in paths:
                value = str(raw_path or "").strip()
                if value and value not in candidate_paths:
                    candidate_paths.append(value)

        for raw_path in candidate_paths:
            if len(added) >= RAG_IMAGE_MAX_ATTACHMENTS:
                break
            try:
                path = Path(raw_path).expanduser().resolve()
                if not path.is_relative_to(runtime_root):
                    logger.warning("skipping RAG image outside runtime data root: %s", path)
                    continue
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    continue
                if not path.is_file() or path.stat().st_size > RAG_IMAGE_MAX_BYTES:
                    logger.warning("skipping missing or oversized RAG image: %s", path)
                    continue
                path_key = str(path)
                if path_key in known_paths:
                    continue
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
                attachment_id = "rag-image-" + hashlib.sha1(path_key.encode()).hexdigest()[:16]
                added.append(
                    Attachment(
                        type="image",
                        base64=encoded,
                        filename=path.name,
                        mime_type=mime_type,
                        id=attachment_id,
                    )
                )
                known_paths.add(path_key)
            except (OSError, ValueError) as exc:
                logger.warning("failed to load retrieved RAG image %s: %s", raw_path, exc)

        if added:
            context.attachments.extend(added)
            context.metadata["_rag_image_paths"] = sorted(known_paths)
        return added


__all__ = [
    "CHAT_EXCLUDED_TOOLS",
    "CHAT_OPTIONAL_TOOLS",
    "KB_SEED_CHARS_PER_KB",
    "KB_SEED_MAX_KBS",
    "AgenticChatPipeline",
    "_DispatchOutcome",
    "_read_int",
]
