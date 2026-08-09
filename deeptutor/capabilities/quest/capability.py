"""
Kid Quest Capability — Gamified Learning Pipeline
==================================================

The ``kid_quest`` capability implements the three-stage gamified learning
pipeline:

  1. **Sourcing** — resolve the knowledge source (public theme or personal
     KB), retrieve corpus chunks.
  2. **Forging** — generate age-appropriate questions from the chunks using
     the ForgingService (LLM or template fallback).
  3. **Questing** — run the turn-based level round: present questions, grade
     answers, provide progressive hints, and settle the game state (stars,
     XP, unlock, streak, badges).

The capability is invoked when a child enters "kid quest" mode.  It reads
the child profile from :class:`UnifiedContext` (``kid_profile_id``,
``kid_age_band``) and the source selection from ``context.metadata``.
"""

from __future__ import annotations

import logging
from typing import Any

from deeptutor.capabilities.quest.forging import AGE_BAND_QUESTION_MATRIX, ForgingService
from deeptutor.capabilities.quest.hints import HintService
from deeptutor.capabilities.quest.quest_cache import (
    compute_content_hash,
    get_cached,
    save_cache,
)
from deeptutor.capabilities.quest.questing import run_quest_round
from deeptutor.core.capability_protocol import BaseCapability, CapabilityManifest
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream_bus import StreamBus
from deeptutor.services.gamification.models import Level, Question, QuestMap
from deeptutor.services.gamification.safety import SafetyFilter
from deeptutor.services.gamification.sources import SourceHandle, SourceResolver

logger = logging.getLogger(__name__)

_SOURCE = "kid_quest"


class KidQuestCapability(BaseCapability):
    """The kid_quest gamified learning capability.

    Manifest:
      - name: ``"kid_quest"``
      - stages: ``["sourcing", "forging", "questing"]``
      - tools: ``["rag", "reason"]``
    """

    manifest = CapabilityManifest(
        name="kid_quest",
        description=(
            "Gamified learning for children: source material → forge questions "
            "→ quest through levels with stars, XP, streaks, and badges."
        ),
        stages=["sourcing", "forging", "questing"],
        cli_aliases=["kid_quest"],
        tools_used=["rag", "reason"],
    )

    def __init__(self) -> None:
        self._forging = ForgingService()
        self._safety = SafetyFilter()
        self._hints_factory = HintService

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        """Execute the three-stage kid_quest pipeline.

        Args:
            context: The unified request context with child profile info.
            stream: The stream bus to emit events to.
        """
        profile_id = context.kid_profile_id or str(context.metadata.get("kid_profile_id", "default-kid"))
        age_band = context.kid_age_band or str(context.metadata.get("kid_age_band", "7-9"))
        language = context.language or "zh"

        # Resolve source
        source_type = str(context.metadata.get("source_type", "public"))
        source_id = str(context.metadata.get("source_id", ""))
        theme_id = str(context.metadata.get("theme_id", source_id))
        level_id = str(context.metadata.get("level_id", "level_1"))
        llm_client = context.metadata.get("llm_client")

        # ---- Stage 1: Sourcing ----
        async with stream.stage("sourcing", source=_SOURCE):
            chunks = await self._sourcing(
                source_type, source_id, theme_id, profile_id, context, stream
            )
            if not chunks:
                # Use default content if no source material
                chunks = [
                    "恐龙是生活在很久很久以前的动物。它们有的很大，有的很小。"
                    "有些恐龙吃植物，有些吃肉。三角龙是一种头上长着三个角的恐龙。",
                    "恐龙蛋是恐龙宝宝出生的地方。恐龙妈妈会找安全的地方下蛋。",
                ] if language == "zh" else [
                    "Dinosaurs lived a very long time ago. Some were big and some were small. "
                    "Some dinosaurs ate plants and some ate meat. Triceratops had three horns.",
                    "Dinosaur eggs are where baby dinosaurs are born. "
                    "Dinosaur mothers find safe places to lay eggs.",
                ]

            await stream.content(
                text=f"📚 已加载学习材料（{len(chunks)} 段）" if language == "zh"
                else f"📚 Loaded {len(chunks)} reading passages",
                source=_SOURCE,
                stage="sourcing",
            )

        # ---- Stage 2: Forging ----
        async with stream.stage("forging", source=_SOURCE):
            questions = await self._forging_stage(
                chunks, age_band, stream, llm_client
            )
            if not questions:
                await stream.error(
                    message="无法生成题目" if language == "zh" else "Failed to generate questions",
                    source=_SOURCE,
                    stage="forging",
                )
                return

            await stream.content(
                text=f"⚔️ 已锻造 {len(questions)} 道题目！" if language == "zh"
                else f"⚔️ Forged {len(questions)} questions!",
                source=_SOURCE,
                stage="forging",
            )

        # ---- Stage 3: Questing ----
        async with stream.stage("questing", source=_SOURCE):
            hint_service = self._hints_factory(safety_filter=self._safety, language=language)
            map_key = context.metadata.get("map_key", f"quest:{source_type}:{source_id or theme_id}")

            result = await run_quest_round(
                questions=questions,
                stream=stream,
                profile_id=profile_id,
                level_id=level_id,
                map_key=str(map_key),
                age_band=age_band,
                hint_service=hint_service,
                safety_filter=self._safety,
                llm_client=llm_client,
                is_boss=bool(context.metadata.get("is_boss", False)),
                is_replay=bool(context.metadata.get("is_replay", False)),
            )

            # Final summary event
            if language == "zh":
                summary = (
                    f"🎉 关卡完成！⭐ {result.stars} 星 | "
                    f"正确 {result.correct_count}/{result.total} | "
                    f"获得 {result.xp_earned} XP"
                )
            else:
                summary = (
                    f"🎉 Level complete! ⭐ {result.stars} stars | "
                    f"Correct {result.correct_count}/{result.total} | "
                    f"Earned {result.xp_earned} XP"
                )
            await stream.content(
                text=summary,
                source=_SOURCE,
                stage="questing",
                metadata={
                    "sub_type": "quest_summary",
                    "result": {
                        "stars": result.stars,
                        "correct_count": result.correct_count,
                        "total": result.total,
                        "xp_earned": result.xp_earned,
                    },
                },
            )

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    async def _sourcing(
        self,
        source_type: str,
        source_id: str,
        theme_id: str,
        profile_id: str,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> list[str]:
        """Resolve the knowledge source and retrieve corpus chunks.

        Args:
            source_type: ``"public"`` or ``"personal"``.
            source_id: Theme ID (public) or KB name (personal).
            theme_id: Fallback theme ID.
            profile_id: Child profile ID (for personal sources).
            context: The unified context.
            stream: The stream bus.

        Returns:
            A list of source text passages.
        """
        handle: SourceHandle | None = None

        if source_type == "personal" and source_id:
            handle = SourceResolver.resolve_personal(profile_id, source_id)
        elif theme_id:
            handle = SourceResolver.resolve_public(theme_id)
        elif source_id:
            handle = SourceResolver.resolve_public(source_id)

        if handle is None:
            return []

        # Retrieve chunks — use the stubbed resolver + inline content
        # In production, this calls the RAG retrieval system.
        chunks = SourceResolver.retrieve(handle, query="")

        # Try loading public theme corpus directly
        if not chunks and handle.is_public:
            chunks = self._load_theme_corpus(handle.identifier)

        return chunks

    def _load_theme_corpus(self, theme_id: str) -> list[str]:
        """Load markdown corpus from a public theme directory.

        Args:
            theme_id: The theme identifier.

        Returns:
            A list of text passages.
        """
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[3]
        theme_dir = project_root / "data" / "public_kb" / theme_id
        if not theme_dir.is_dir():
            return []

        chunks: list[str] = []
        for md_file in sorted(theme_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8").strip()
                if text:
                    chunks.append(text)
            except OSError:
                continue
        return chunks

    async def _forging_stage(
        self,
        chunks: list[str],
        age_band: str,
        stream: StreamBus,
        llm_client: Any | None = None,
    ) -> list[Question]:
        """Generate questions from source material.

        Args:
            chunks: Source text passages.
            age_band: Target age band.
            stream: The stream bus.
            llm_client: Optional LLM client.

        Returns:
            A list of validated :class:`Question` objects.
        """
        # Check cache
        content_hash = compute_content_hash(chunks, age_band, AGE_BAND_QUESTION_MATRIX.get(age_band, []))
        cached_map = get_cached(content_hash)
        if cached_map is not None and cached_map.levels:
            # Return questions from the first cached level
            return list(cached_map.levels[0].questions)

        # Generate
        q_types = AGE_BAND_QUESTION_MATRIX.get(age_band, AGE_BAND_QUESTION_MATRIX["7-9"])
        questions = await self._forging.generate_questions(
            chunks=chunks,
            age_band=age_band,
            q_types=q_types,
            llm_client=llm_client,
            count=5,
        )

        # Cache the result
        if questions:
            level = Level(
                level_id="level_1",
                title="Quest Level 1",
                questions=list(questions),
            )
            quest_map = QuestMap(
                map_id=content_hash,
                title="Generated Quest",
                levels=[level],
            )
            try:
                save_cache(content_hash, quest_map)
            except Exception:
                logger.warning("Failed to save quest cache", exc_info=True)

        return questions


__all__ = ["KidQuestCapability"]
