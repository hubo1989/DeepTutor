"""Document loading for the LlamaIndex RAG pipeline.

Parser-backed files (PDF / Office / e-book) are converted through the shared
document-parse bridge (``deeptutor/services/parsing``), so the engine the user
picked in Settings → Document Parsing (text-only, MinerU, Docling, markitdown,
PyMuPDF4LLM) owns extraction. This is the same seam LightRAG and GraphRAG use;
routing LlamaIndex through it too means the parse-engine choice is honored by
every local retrieval engine, and image-capable engines' extracted images flow
into the multimodal ``ImageNode`` path below.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import logging
import mimetypes
from pathlib import Path
from typing import Any, Callable, Iterable

from llama_index.core import Document
from llama_index.core.schema import ImageNode

from deeptutor.services.embedding import get_embedding_client
from deeptutor.services.llm.client import get_llm_client
from deeptutor.services.rag.file_routing import FileTypeRouter
from deeptutor.utils.document_validator import DocumentValidator

from .config import image_description_limits

IMAGE_DESCRIPTION_SYSTEM_PROMPT = (
    "You describe images for a retrieval-augmented knowledge base. "
    "Be factual, concise, and include any visible text, labels, diagrams, "
    "tables, logos, or important visual relationships. Do not invent details."
)

IMAGE_DESCRIPTION_PROMPT = (
    "Describe this image so that a text-only answer generator can understand "
    "and cite it later. Include visible text/OCR if present, the main subject, "
    "and any educational or technical meaning. Keep the answer under 180 words."
)


@dataclass(frozen=True)
class _ImageSource:
    """An image to embed as an ``ImageNode``, plus the document it came from.

    ``path`` is the image file on disk (what gets embedded and served).
    ``origin`` is the document it belongs to: the image itself for a standalone
    image file, or the source PDF/e-book for an image extracted during parsing —
    so retrieval cites the source document rather than an opaque cache asset.
    """

    path: Path
    origin: Path


class LlamaIndexDocumentLoader:
    """Convert source files into LlamaIndex ``Document`` / ``ImageNode`` objects."""

    def __init__(self, logger=None, image_concurrency: int = 6) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.image_concurrency = max(1, int(image_concurrency))

    async def load(
        self,
        file_paths: Iterable[str],
        image_progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Any]:
        documents: list[Any] = []
        image_sources: list[_ImageSource] = []
        classification = FileTypeRouter.classify_files(list(file_paths))

        for file_path_str in classification.parser_files:
            file_path = Path(file_path_str)
            self.logger.info(f"Parsing document: {file_path.name}")
            # MinerU cloud parsing blocks end to end (upload + 300s polling +
            # archive download) on a synchronous httpx.Client — running it on
            # the event loop stalls every other request for the whole PDF
            # (same class of bug as upstream #761/#777). Hand it to a thread.
            text, extracted_images, page_texts, page_image_paths = await asyncio.to_thread(
                self._parse_document, file_path
            )
            if page_texts:
                for page_label, page_text in page_texts:
                    self._append_if_nonempty(
                        documents,
                        file_path,
                        page_text,
                        extra_metadata={
                            "page_label": page_label,
                            "page": page_label,
                            "image_paths": page_image_paths.get(page_label, []),
                        },
                    )
            else:
                self._append_if_nonempty(
                    documents,
                    file_path,
                    text,
                    extra_metadata={
                        "image_paths": [str(source.path) for source in extracted_images],
                    },
                )
            image_sources.extend(extracted_images)

        for file_path_str in classification.text_files:
            file_path = Path(file_path_str)
            self.logger.info(f"Parsing text: {file_path.name}")
            text = await FileTypeRouter.read_text_file(str(file_path))
            self._append_if_nonempty(documents, file_path, text)

        for file_path_str in classification.image_files:
            path = Path(file_path_str)
            image_sources.append(_ImageSource(path=path, origin=path))

        if image_sources:
            documents.extend(
                await self._load_image_nodes(
                    image_sources, image_progress_callback=image_progress_callback
                )
            )

        for file_path_str in classification.unsupported:
            self.logger.warning(f"Skipped unsupported file: {Path(file_path_str).name}")

        return documents

    def _parse_document(
        self, file_path: Path
    ) -> tuple[str, list[_ImageSource], list[tuple[str, str]], dict[str, list[str]]]:
        """Parse a document through the shared, engine-pluggable parse layer.

        Returns ``(text, extracted_images, page_texts, page_image_paths)``. ``page_texts`` is
        populated when the parser emits structured blocks with zero-based
        ``page_idx`` values, allowing retrieval results to retain page-level
        citation metadata. A parse failure (engine
        unavailable, unsupported format for the active engine, or models not
        ready) is logged and the file is skipped — matching the sibling
        LightRAG/GraphRAG pipelines — rather than aborting the whole batch.
        """
        from deeptutor.services.parsing import ParserError, get_parse_service

        try:
            parsed = get_parse_service().parse(file_path)
        except ParserError as exc:
            self.logger.warning(
                f"Skipped {file_path.name}: the active document-parsing engine could "
                f"not handle it ({exc}). Change the engine in Settings → Document Parsing."
            )
            return "", [], [], {}

        text = parsed.markdown.strip() or self._text_from_blocks(parsed.blocks)
        page_texts = self._page_texts_from_blocks(parsed.blocks)
        images = self._collect_asset_images(parsed.asset_dir, origin=file_path)
        page_image_paths = self._page_image_paths_from_blocks(parsed.blocks, parsed.asset_dir)
        return text, images, page_texts, page_image_paths

    @staticmethod
    def _page_image_paths_from_blocks(
        blocks: list[dict] | None,
        asset_dir: Path | None,
    ) -> dict[str, list[str]]:
        """Map parser image blocks to safe, real asset paths by source page.

        MinerU stores ``img_path`` as a relative path such as
        ``images/figure-1.jpg``. The RAG index only needs the local path as
        provenance; the bytes are loaded later, when a Vision-capable answer
        is actually requested. Paths are constrained to ``asset_dir`` so a
        malformed parser block cannot make the chat layer read an arbitrary
        file.
        """
        if not blocks or not asset_dir or not Path(asset_dir).is_dir():
            return {}

        root = Path(asset_dir).resolve()
        grouped: dict[str, list[str]] = {}
        for block in blocks:
            if not isinstance(block, dict):
                continue
            raw_img_path = str(block.get("img_path") or "").strip()
            if not raw_img_path:
                continue
            try:
                page_idx = int(block.get("page_idx"))
            except (TypeError, ValueError):
                continue
            if page_idx < 0:
                continue

            candidate = Path(raw_img_path)
            if not candidate.is_absolute():
                candidate = root / candidate.name
            try:
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root):
                    continue
            except OSError:
                continue
            if (
                not resolved.is_file()
                or resolved.suffix.lower() not in FileTypeRouter.IMAGE_EXTENSIONS
            ):
                continue

            page_label = str(page_idx + 1)
            paths = grouped.setdefault(page_label, [])
            value = str(resolved)
            if value not in paths:
                paths.append(value)
        return grouped

    @staticmethod
    def _text_from_blocks(blocks: list[dict] | None) -> str:
        """Fall back to concatenating block text when an engine emits no markdown."""
        if not blocks:
            return ""
        parts = [
            LlamaIndexDocumentLoader._block_text(block)
            for block in blocks
            if isinstance(block, dict)
        ]
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _block_text(block: dict) -> str:
        """Return indexable Markdown for one structured parser block."""
        parts: list[str] = []
        seen: set[str] = set()

        def append_values(value: Any) -> None:
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = str(item or "").strip()
                if text and text not in seen:
                    parts.append(text)
                    seen.add(text)

        append_values(block.get("text"))
        append_values(block.get("content"))

        block_type = str(block.get("type") or "").lower()
        img_path = str(block.get("img_path") or "").strip()
        if block_type == "table":
            append_values(block.get("table_caption"))
            append_values(block.get("table_body"))
            if not block.get("table_body") and img_path:
                append_values(f"[Table image on source page: {Path(img_path).name}]")
            append_values(block.get("table_footnote"))
        elif block_type == "image":
            # Preserve the fact that a source page contains a visual even when
            # the active text-only models cannot describe or embed it.
            if img_path:
                append_values(f"[Image on source page: {Path(img_path).name}]")
            append_values(block.get("image_caption"))
            append_values(block.get("image_footnote"))
        else:
            # Keep parser-added captions even if a future engine attaches them
            # to a non-standard block type.
            append_values(block.get("image_caption"))
            append_values(block.get("image_footnote"))
            append_values(block.get("table_caption"))
            append_values(block.get("table_body"))
            append_values(block.get("table_footnote"))

        return "\n\n".join(parts)

    @staticmethod
    def _page_texts_from_blocks(blocks: list[dict] | None) -> list[tuple[str, str]]:
        """Group structured parser text into one document per source page.

        MinerU emits zero-based ``page_idx`` values on content-list blocks.
        If any meaningful text block lacks a valid page index, return an empty
        list so callers safely fall back to the parser's complete markdown.
        Repeated headers, footers, and printed page numbers are omitted because
        they add retrieval noise without useful teaching content.
        """
        if not blocks:
            return []

        grouped: dict[int, list[str]] = {}
        noise_types = {"header", "footer", "page_number"}
        saw_indexable_content = False

        for block in blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").lower() in noise_types:
                continue

            part = LlamaIndexDocumentLoader._block_text(block)
            if not part:
                continue
            saw_indexable_content = True

            raw_page_idx = block.get("page_idx")
            try:
                page_idx = int(raw_page_idx)
            except (TypeError, ValueError):
                return []
            if page_idx < 0:
                return []
            grouped.setdefault(page_idx, []).append(part)

        if not saw_indexable_content:
            return []

        return [
            (str(page_idx + 1), "\n\n".join(parts))
            for page_idx, parts in sorted(grouped.items())
            if parts
        ]

    def _collect_asset_images(self, asset_dir: Path | None, *, origin: Path) -> list[_ImageSource]:
        """Gather images the parse engine extracted into ``asset_dir``.

        Engines that don't extract images (text-only, markitdown) leave
        ``asset_dir`` empty, so this returns nothing and the document is indexed
        as text alone.
        """
        if not asset_dir or not Path(asset_dir).is_dir():
            return []
        images = [
            _ImageSource(path=child, origin=origin)
            for child in sorted(Path(asset_dir).iterdir())
            if child.is_file() and child.suffix.lower() in FileTypeRouter.IMAGE_EXTENSIONS
        ]
        if images:
            self.logger.info(
                f"Extracted {len(images)} image(s) from {origin.name} for multimodal indexing"
            )
        return images

    async def _load_image_nodes(
        self,
        sources: list[_ImageSource],
        *,
        image_progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[ImageNode]:
        try:
            embedding_client = get_embedding_client()
        except Exception as exc:
            self._log_skipped_images(sources, f"embedding client is unavailable ({exc})")
            return []
        if not embedding_client.supports_multimodal_contents():
            self._log_skipped_images(
                sources,
                "embedding provider/model does not support multimodal contents "
                f"(binding={embedding_client.config.binding}, "
                f"model={embedding_client.config.model})",
            )
            return []

        # Resolve the LLM only after the embedding prerequisite passes. This
        # keeps text-only embedding setups independent of LLM configuration and
        # reuses one client for the whole image batch.
        try:
            llm_client = get_llm_client()
        except Exception as exc:
            self._log_skipped_images(sources, f"LLM client is unavailable ({exc})")
            return []
        if not llm_client.supports_multimodal_images():
            self._log_skipped_images(
                sources,
                "LLM provider/model does not support multimodal image input "
                f"(binding={llm_client.config.binding}, model={llm_client.config.model})",
            )
            return []

        embedded: list[_ImageSource] = []
        descriptions: list[str] = []
        contents: list[dict[str, str]] = []
        completed = 0
        total = len(sources)
        concurrency, timeout_seconds = image_description_limits()
        semaphore = asyncio.Semaphore(concurrency)

        async def _describe_one(
            source: _ImageSource,
        ) -> tuple[_ImageSource, str, dict[str, str]] | None:
            nonlocal completed
            result: tuple[_ImageSource, str, dict[str, str]] | None = None
            try:
                try:
                    async with semaphore:
                        image_payload = self._load_image_payload(source.path)
                        description = await asyncio.wait_for(
                            self._describe_image(
                                llm_client,
                                source.path,
                                image_payload["base64"],
                                image_payload["mimetype"],
                            ),
                            timeout=timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    self.logger.error(
                        "Image description timed out after %ss: %s",
                        timeout_seconds,
                        source.path.name,
                    )
                except OSError as exc:
                    self.logger.error(f"Failed to read image {source.path.name}: {exc}")
                except Exception as exc:
                    self.logger.error(
                        "Failed to describe image %s with configured multimodal LLM "
                        "(binding=%s, model=%s): %s",
                        source.path.name,
                        llm_client.config.binding,
                        llm_client.config.model,
                        exc,
                    )
                else:
                    if not description:
                        self.logger.warning(
                            "Skipped image because the configured multimodal LLM "
                            f"returned no description: {source.path.name}"
                        )
                    else:
                        result = (
                            source,
                            description,
                            {"image": image_payload["data_uri"]},
                        )
            finally:
                completed += 1
                if image_progress_callback:
                    try:
                        image_progress_callback(completed, total)
                    except Exception:
                        pass
            return result

        # gather preserves input order, so embedded/descriptions/contents stay
        # aligned regardless of completion order.
        results = await asyncio.gather(*(_describe_one(source) for source in sources))
        for result in results:
            if result is None:
                continue
            embedded.append(result[0])
            descriptions.append(result[1])
            contents.append(result[2])

        if not contents:
            return []

        try:
            embeddings = await embedding_client.embed_contents(contents)
        except Exception as exc:
            self.logger.error(
                "Failed to embed image contents with configured multimodal embedding "
                "provider/model (binding=%s, model=%s): %s",
                embedding_client.config.binding,
                embedding_client.config.model,
                exc,
            )
            return []
        nodes: list[ImageNode] = []
        for source, description, embedding in zip(embedded, descriptions, embeddings):
            mimetype = mimetypes.guess_type(source.path.name)[0] or "application/octet-stream"
            nodes.append(
                ImageNode(
                    text=f"[Image] {source.origin.name}\n\n{description}",
                    image_path=str(source.path),
                    image_mimetype=mimetype,
                    metadata={
                        "file_name": source.origin.name,
                        "file_path": str(source.origin),
                        "content_type": "image",
                        "image_description": description,
                    },
                    embedding=embedding,
                )
            )
            self.logger.info(f"Loaded image: {source.path.name} ({len(embedding)}D vector)")
        return nodes

    def _log_skipped_images(self, sources: list[_ImageSource], reason: str) -> None:
        for source in sources:
            self.logger.warning(
                "Skipped image because image indexing requires both multimodal "
                f"embedding and multimodal LLM support; {reason}: {source.path.name}"
            )

    async def _describe_image(
        self, llm_client: Any, file_path: Path, image_base64: str, mimetype: str
    ) -> str:
        response = await llm_client.complete(
            IMAGE_DESCRIPTION_PROMPT,
            system_prompt=IMAGE_DESCRIPTION_SYSTEM_PROMPT,
            image_data=image_base64,
            image_mime_type=mimetype,
            image_filename=file_path.name,
        )
        return response.strip()

    def _load_image_payload(self, file_path: Path) -> dict[str, str]:
        size = file_path.stat().st_size
        if size > DocumentValidator.MAX_FILE_SIZE:
            raise OSError(
                f"image file too large: {size} bytes; "
                f"maximum allowed: {DocumentValidator.MAX_FILE_SIZE} bytes"
            )
        mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return {
            "base64": encoded,
            "data_uri": f"data:{mimetype};base64,{encoded}",
            "mimetype": mimetype,
        }

    def _append_if_nonempty(
        self,
        documents: list[Any],
        file_path: Path,
        text: str,
        *,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        if text.strip():
            metadata = {
                "file_name": file_path.name,
                "file_path": str(file_path),
            }
            if extra_metadata:
                metadata.update(extra_metadata)
            excluded_metadata_keys = []
            if metadata.get("image_paths"):
                # Keep paths available for post-retrieval Vision attachments,
                # but do not serialize long cache paths into the text used by
                # LlamaIndex's embed/LLM metadata token budget.
                excluded_metadata_keys.append("image_paths")
            documents.append(
                Document(
                    text=text,
                    metadata=metadata,
                    excluded_embed_metadata_keys=excluded_metadata_keys,
                    excluded_llm_metadata_keys=excluded_metadata_keys,
                )
            )
            self.logger.info(f"Loaded: {file_path.name} ({len(text)} chars)")
        else:
            self.logger.warning(f"Skipped empty document: {file_path.name}")
