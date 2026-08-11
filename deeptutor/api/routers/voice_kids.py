"""
Kids TTS (Text-to-Speech) Router
=================================

Exposes a simple TTS endpoint for the gamified learning UI. Younger children
(7-9 age band) get TTS enabled by default to assist reading comprehension.

Endpoint:
    POST /api/v1/kids/tts  — synthesize speech from text.

When the backend voice service is configured, it delegates to
:func:`deeptutor.services.voice.synthesize_speech`. When the voice service
is not available (no TTS provider configured), it returns HTTP 501 so the
frontend can fall back to the browser-native ``speechSynthesis`` API.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.kid_context import get_kid_context_or_default
from deeptutor.multi_user.models import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TTSRequest(BaseModel):
    """Request body for POST /kids/tts."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Text to synthesize (max 500 chars for child-appropriate length).",
    )
    age_band: str = Field(
        default="7-9",
        description='Target age band: "7-9", "10-12", or "13-15".',
    )
    profile_id: str = Field(
        default="",
        description="Optional profile ID for metrics tracking.",
    )


class TTSResponse(BaseModel):
    """Response for POST /kids/tts.

    When ``audio_base64`` is present, the client can play it directly.
    When the backend TTS is unavailable, ``fallback`` is ``True`` and the
    client should use the browser-native ``speechSynthesis`` API.
    """

    audio_base64: str = ""
    content_type: str = "audio/mpeg"
    fallback: bool = False
    message: str = ""


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

# Age bands where TTS is enabled by default.
TTS_DEFAULT_AGE_BANDS: set[str] = {"7-9"}


@router.post("/kids/tts", response_model=TTSResponse)
async def kids_tts(
    body: TTSRequest,
    user: CurrentUser = Depends(get_current_user),
) -> TTSResponse:
    """Synthesize speech from text for a child learner.

    For 7-9 age band, TTS is on by default. The endpoint attempts to use
    the backend voice service; if unavailable, returns ``fallback=True``
    so the frontend uses browser-native ``speechSynthesis``.

    Records a ``tts_used`` metric for analytics.
    """
    kid_ctx = get_kid_context_or_default()
    profile_id = body.profile_id or kid_ctx.profile_id or user.id

    # Record the metric (best-effort, non-blocking)
    try:
        from deeptutor.services.gamification.metrics import record_metric

        record_metric(
            "tts_used",
            {
                "age_band": body.age_band,
                "text_length": len(body.text),
                "default_enabled": body.age_band in TTS_DEFAULT_AGE_BANDS,
            },
            profile_id=profile_id,
        )
    except Exception:
        logger.debug("Failed to record tts_used metric", exc_info=True)

    # Attempt backend TTS synthesis
    try:
        import base64

        from deeptutor.services.voice import synthesize_speech

        audio_bytes, content_type = await synthesize_speech(
            body.text,
        )
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return TTSResponse(
            audio_base64=audio_b64,
            content_type=content_type,
            fallback=False,
            message="OK",
        )
    except ImportError:
        logger.info("Voice service not available; returning fallback=True")
        return TTSResponse(
            fallback=True,
            message="Backend TTS unavailable. Use browser speechSynthesis.",
        )
    except Exception as exc:
        logger.warning("TTS synthesis failed: %s", exc, exc_info=True)
        # Return fallback so the child still gets audio via browser
        return TTSResponse(
            fallback=True,
            message=f"TTS provider error: {exc}",
        )


__all__ = ["router"]
