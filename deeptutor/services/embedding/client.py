"""Unified embedding client backed by normalized provider runtime config."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import math
import threading
from typing import Any, Dict, List, Mapping, Optional

from deeptutor.services.config.embedding_endpoint import (
    redact_embedding_endpoint_for_display,
)
from deeptutor.services.config.provider_runtime import (
    EMBEDDING_PROVIDERS,
    embedding_endpoint_validation_error,
)

from .adapters import ADAPTER_BACKENDS, BaseEmbeddingAdapter, EmbeddingRequest
from .config import EmbeddingConfig, get_embedding_config
from .validation import validate_embedding_batch


def _resolve_adapter_class(binding: str) -> type[BaseEmbeddingAdapter]:
    provider = (binding or "").strip().lower()
    spec = EMBEDDING_PROVIDERS.get(provider)
    if spec is None:
        supported = sorted(EMBEDDING_PROVIDERS.keys())
        raise ValueError(
            f"Unknown embedding binding: '{binding}'. Supported: {', '.join(supported)}"
        )
    cls = ADAPTER_BACKENDS.get(spec.adapter)
    if cls is None:
        raise ValueError(
            f"No adapter registered for backend '{spec.adapter}' (binding='{binding}')"
        )
    return cls


def _estimate_text_tokens(texts: List[str]) -> int:
    """Estimate provider input tokens before an embedding request.

    Embedding providers do not share a response usage schema, so the hard
    preflight reservation uses a conservative character-based estimate and
    reconciles with provider usage when available.
    """
    chars = sum(len(text or "") for text in texts)
    return max(1, math.ceil(chars / 3.5))


def _estimate_content_tokens(contents: List[Dict[str, Any]]) -> int:
    text_items = [str(item.get("text") or "") for item in contents]
    estimated = _estimate_text_tokens(text_items)
    # Image/video-only embedding requests still consume provider work but may
    # expose no token count. Count each such item minimally for quota purposes.
    return max(estimated, sum(1 for item in contents if not item.get("text")))


def _commercial_text_token_bound(texts: List[str]) -> int:
    """UTF-8 bytes are a provider-neutral upper bound for tokenizer pieces."""

    return max(
        _estimate_text_tokens(texts), sum(len((text or "").encode("utf-8")) for text in texts)
    )


def _commercial_content_token_bound(contents: List[Dict[str, Any]]) -> int:
    try:
        serialized = json.dumps(contents, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(contents)
    return max(_estimate_content_tokens(contents), len(serialized.encode("utf-8")))


def _batch_request_id(request_id: str | None, batch_index: int) -> str | None:
    """Derive a stable reservation id for one batch of a retried operation."""

    normalized = str(request_id or "").strip()
    return f"{normalized}/batch-{batch_index + 1}" if normalized else None


def _usage_tokens(usage: Any) -> int:
    if isinstance(usage, Mapping):
        for key in ("total_tokens", "prompt_tokens", "input_tokens", "tokens"):
            value = usage.get(key)
            try:
                if value is not None and int(value) > 0:
                    return int(value)
            except (TypeError, ValueError):
                continue
    for key in ("total_tokens", "prompt_tokens", "input_tokens", "tokens"):
        value = getattr(usage, key, None)
        try:
            if value is not None and int(value) > 0:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _estimate_text_tokens(texts: List[str]) -> int:
    """Estimate provider input tokens before an embedding request.

    Embedding providers do not share a response usage schema, so the hard
    preflight reservation uses a conservative character-based estimate and
    reconciles with provider usage when available.
    """
    chars = sum(len(text or "") for text in texts)
    return max(1, math.ceil(chars / 3.5))


def _estimate_content_tokens(contents: List[Dict[str, Any]]) -> int:
    text_items = [str(item.get("text") or "") for item in contents]
    estimated = _estimate_text_tokens(text_items)
    # Image/video-only embedding requests still consume provider work but may
    # expose no token count. Count each such item minimally for quota purposes.
    return max(estimated, sum(1 for item in contents if not item.get("text")))


def _commercial_text_token_bound(texts: List[str]) -> int:
    """UTF-8 bytes are a provider-neutral upper bound for tokenizer pieces."""

    return max(
        _estimate_text_tokens(texts), sum(len((text or "").encode("utf-8")) for text in texts)
    )


def _commercial_content_token_bound(contents: List[Dict[str, Any]]) -> int:
    try:
        serialized = json.dumps(contents, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(contents)
    return max(_estimate_content_tokens(contents), len(serialized.encode("utf-8")))


def _batch_request_id(request_id: str | None, batch_index: int) -> str | None:
    """Derive a stable reservation id for one batch of a retried operation."""

    normalized = str(request_id or "").strip()
    return f"{normalized}/batch-{batch_index + 1}" if normalized else None


def _usage_tokens(usage: Any) -> int:
    if isinstance(usage, Mapping):
        for key in ("total_tokens", "prompt_tokens", "input_tokens", "tokens"):
            value = usage.get(key)
            try:
                if value is not None and int(value) > 0:
                    return int(value)
            except (TypeError, ValueError):
                continue
    for key in ("total_tokens", "prompt_tokens", "input_tokens", "tokens"):
        value = getattr(usage, key, None)
        try:
            if value is not None and int(value) > 0:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


class EmbeddingClient:
    """Unified embedding client for RAG and retrieval services."""

    # 发帖节流：线程级锁串行化"等待间隔+发帖"，跨线程/跨 event loop 互斥
    # （asyncio 锁在新 loop 模型下失效的教训）。
    _spacing_lock: Any = None
    _last_request_monotonic: float = 0.0
    _thread_guard: Any = None

    @classmethod
    def _global_spacing_lock(cls):
        import threading

        if cls._spacing_lock is None:
            cls._thread_guard = threading.Lock()
            with cls._thread_guard:
                if cls._spacing_lock is None:
                    from threading import Lock as _TLock

                    cls._spacing_lock = _TLock()
        return cls._spacing_lock

    @staticmethod
    def _hold_spacing_lock():
        """Blocking acquire — cross-thread, loop-agnostic."""
        lock = EmbeddingClient._global_spacing_lock()
        lock.acquire()
        return lock

        # 每个线程经 _run_in_new_loop 跑独立 event loop——asyncio.Lock 绑定
        # 创建时的 loop，跨 loop 既不互斥还会挂死。必须用线程级锁。
        _spacing_lock: Any = None
        _last_request_monotonic: float = 0.0
        _thread_guard: Any = None

        @classmethod
        def _global_spacing_lock(cls):
            import threading

            if cls._spacing_lock is None:
                cls._thread_guard = threading.Lock()
                with cls._thread_guard:
                    if cls._spacing_lock is None:
                        from threading import Lock as _TLock

                        cls._spacing_lock = _TLock()
            return cls._spacing_lock

        @staticmethod
        def _hold_spacing_lock():
            """Blocking acquire — cross-thread, loop-agnostic."""
            lock = EmbeddingClient._global_spacing_lock()
            lock.acquire()
            return lock

    # 全局发帖节流：KB reindex 时 LlamaIndex 用线程池并发调 embedding，
    # 每个线程经 _run_in_new_loop 跑独立 event loop——asyncio.Lock 绑定
    # 创建时的 loop，跨 loop 既不互斥还会挂死。必须用线程级锁。
    _spacing_lock: Any = None
    _last_request_monotonic: float = 0.0
    _thread_guard = threading.Lock()

    @classmethod
    def _global_spacing_lock(cls):
        with cls._thread_guard:
            if cls._spacing_lock is None:
                cls._spacing_lock = threading.Lock()
        return cls._spacing_lock

    @staticmethod
    @asynccontextmanager
    async def _hold_spacing_lock():
        """Acquire the cross-thread lock without blocking the current loop."""
        import asyncio

        lock = EmbeddingClient._global_spacing_lock()
        while not lock.acquire(blocking=False):
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            lock.release()

    async def _wait_for_request_slot(self) -> None:
        """Space request starts across threads/loops without serializing HTTP.

        Text and multimodal calls share the same clock. No delay means no
        throttling; adapters still enforce their own concurrency/retry policy.
        """
        import asyncio
        from time import monotonic

        delay = self.config.batch_delay
        if delay <= 0:
            return
        async with EmbeddingClient._hold_spacing_lock():
            remaining = delay - (monotonic() - EmbeddingClient._last_request_monotonic)
            if remaining > 0:
                await asyncio.sleep(remaining)
            EmbeddingClient._last_request_monotonic = monotonic()

    def __init__(self, config: Optional[EmbeddingConfig] = None):
        self.config = config or get_embedding_config()
        self.logger = logging.getLogger(__name__)
        endpoint = self.config.effective_url or self.config.base_url
        problem = embedding_endpoint_validation_error(self.config.binding, endpoint)
        if problem:
            displayed_endpoint = redact_embedding_endpoint_for_display(endpoint)
            raise ValueError(
                f"{problem} Current Settings endpoint is {displayed_endpoint!r}. "
                "DeepTutor sends embedding requests to the Settings URL exactly; "
                "update the visible Endpoint URL instead of relying on hidden path appending."
            )
        adapter_class = _resolve_adapter_class(self.config.binding)
        self.adapter = adapter_class(
            {
                "api_key": self.config.api_key,
                "base_url": self.config.effective_url or self.config.base_url,
                "api_version": self.config.api_version,
                "model": self.config.model,
                "dimensions": self.config.dim,
                "send_dimensions": self.config.send_dimensions,
                "request_timeout": self.config.request_timeout,
                "extra_headers": self.config.extra_headers or {},
            }
        )
        self.logger.info(
            f"Initialized embedding client with {self.config.binding} adapter "
            f"(model: {self.config.model}, dimensions: {self.config.dim})"
        )

    async def embed(
        self,
        texts: List[str],
        progress_callback=None,
        *,
        input_type: str | None = None,
        request_id: str | None = None,
    ) -> List[List[float]]:
        """Embed text batches, optionally identifying their retrieval role."""
        if not texts:
            return []

        # Only adapters that opted in receive the role. Forwarding it to every
        # backend would change the request Jina has always sent (no `task`) and
        # silently invalidate the indexes built from it.
        role = input_type if getattr(self.adapter, "SUPPORTS_INPUT_TYPE", False) else None

        # Clamp configured batch size against the provider's per-request item
        # cap. SiliconFlow Qwen3 family caps at 32; DashScope at 20; others
        # have generous defaults. Without this clamp, indexing a doc with many
        # chunks fails on the second batch even when "Test connection" passes.
        spec = EMBEDDING_PROVIDERS.get(self.config.binding)
        provider_max = spec.max_batch_items if spec else 256
        batch_size = max(1, min(self.config.batch_size, provider_max))
        if batch_size < self.config.batch_size:
            self.logger.info(
                f"Clamped batch_size {self.config.batch_size} -> {batch_size} "
                f"(provider '{self.config.binding}' max={provider_max})"
            )
        all_embeddings: List[List[float]] = []
        expected_dim: int | None = None

        total_batches = (len(texts) + batch_size - 1) // batch_size
        for i, start in enumerate(range(0, len(texts), batch_size)):
            batch = texts[start : start + batch_size]
            request = EmbeddingRequest(
                texts=batch,
                model=self.config.model,
                dimensions=self.config.dim or None,
                input_type=role,
            )
            estimated_tokens = _estimate_text_tokens(batch)
            lease = self._reserve_quota(estimated_tokens)
            commercial_quantity = _commercial_text_token_bound(batch)
            try:
                commercial_lease = await self._reserve_commercial(
                    commercial_quantity,
                    request_id=_batch_request_id(request_id, i),
                )
            except BaseException:
                if lease is not None:
                    lease.release()
                raise
            try:
                await self._wait_for_request_slot()
                response = await self.adapter.embed(request)
            except BaseException as exc:
                await self._release_leases(lease, commercial_lease)
                if not isinstance(exc, Exception):
                    raise
                import traceback as _tb

                self.logger.error(
                    f"Embedding batch failed "
                    f"(binding={self.config.binding}, model={self.config.model}, "
                    f"batch_index={i + 1}/{total_batches}): {exc}\n{_tb.format_exc()}"
                )
                raise
            except Exception as exc:
                # Capture batch context so the task log stream / KB diagnostics
                # show actionable info instead of a bare exception string.
                import traceback

                first_chunk_chars = len(batch[0]) if batch else 0
                longest_chunk_chars = max((len(t) for t in batch), default=0)
                self.logger.error(
                    f"Embedding batch failed "
                    f"(binding={self.config.binding}, model={self.config.model}, "
                    f"batch_index={i + 1}/{total_batches}, batch_items={len(batch)}, "
                    f"first_chunk_chars={first_chunk_chars}, "
                    f"longest_chunk_chars={longest_chunk_chars}): {exc}\n"
                    f"{traceback.format_exc()}"
                )
                raise
            validated = validate_embedding_batch(
                response.embeddings,
                expected_count=len(batch),
                binding=self.config.binding,
                model=self.config.model,
                batch_index=i + 1,
                total_batches=total_batches,
                start_index=start,
            )
            await self._finalize_leases(
                lease,
                commercial_lease,
                getattr(response, "usage", None),
                estimated_tokens=estimated_tokens,
                commercial_quantity=commercial_quantity,
            )
            batch_dim = len(validated[0]) if validated else 0
            if expected_dim is None:
                expected_dim = batch_dim
            elif batch_dim != expected_dim:
                raise ValueError(
                    "Embedding provider returned inconsistent vector dimensions "
                    f"across batches (binding={self.config.binding}, "
                    f"model={self.config.model}): expected {expected_dim}, "
                    f"got {batch_dim} in batch {i + 1}/{total_batches}. "
                    "Use a single embedding model/dimension and re-index the knowledge base."
                )

            all_embeddings.extend(validated)

            # Report progress after each batch
            if progress_callback:
                try:
                    progress_callback(i + 1, total_batches)
                except Exception as exc:
                    # Progress feedback is best-effort: never fail the embedding
                    # run, but never lose the failure silently either — index
                    # progress would read as stalled with no trace why.
                    self.logger.warning(
                        f"Embedding progress callback failed "
                        f"(batch {i + 1}/{total_batches}): {exc}",
                        exc_info=True,
                    )

        self.logger.debug(
            f"Generated {len(all_embeddings)} embeddings using "
            f"{self.config.binding} (batch_size={batch_size})"
        )
        return all_embeddings

    def _reserve_quota(self, estimated_tokens: int):
        if self.config.source == "byok":
            from deeptutor.multi_user.byok_usage import start_byok_usage
            from deeptutor.multi_user.context import get_current_user
            from deeptutor.multi_user.execution_source import get_execution_source

            source = get_execution_source()
            source_for_service = source if source and source.service == "embedding" else None
            return start_byok_usage(
                service="embedding",
                user_id=source_for_service.user_id if source_for_service else get_current_user().id,
                profile_id=(
                    source_for_service.profile_id
                    if source_for_service and source_for_service.profile_id
                    else self.config.profile_id or "unknown"
                ),
                provider=self.config.provider_name or self.config.binding,
                model=self.config.model,
                estimated_units=estimated_tokens,
            )
        from deeptutor.multi_user.token_quota import reserve_current_user_units

        return reserve_current_user_units(
            resource="embedding",
            requested_units=estimated_tokens,
        )

    @staticmethod
    def _finalize_quota(lease: Any, usage: Any, estimated_tokens: int) -> None:
        if lease is not None:
            lease.finalize(_usage_tokens(usage) or estimated_tokens)

    async def _reserve_commercial(
        self,
        quantity: int,
        *,
        request_id: str | None = None,
    ):
        from deeptutor.commercial.metering import (
            CommercialUsageLimitExceeded,
            CommercialUsageUnavailable,
            reserve_commercial_usage,
        )

        from .adapters import EmbeddingProviderError

        try:
            return await reserve_commercial_usage(
                meter="embedding_tokens",
                quantity=quantity,
                source=self.config.source,
                provider=self.config.provider_name or self.config.binding,
                model=self.config.model,
                request_id=request_id,
            )
        except CommercialUsageLimitExceeded as exc:
            raise EmbeddingProviderError(
                str(exc),
                status=429,
                model=self.config.model,
                provider="deeptutor",
            ) from exc
        except CommercialUsageUnavailable as exc:
            raise EmbeddingProviderError(
                str(exc),
                status=503,
                model=self.config.model,
                provider="deeptutor",
            ) from exc

    @staticmethod
    async def _release_leases(lease: Any, commercial_lease: Any) -> None:
        """Release both ledgers even when the legacy SQLite release fails."""

        try:
            if lease is not None:
                lease.release()
        finally:
            if commercial_lease is not None:
                await commercial_lease.release()

    async def _finalize_leases(
        self,
        lease: Any,
        commercial_lease: Any,
        usage: Any,
        *,
        estimated_tokens: int,
        commercial_quantity: int,
    ) -> None:
        """Settle PostgreSQL even when the legacy SQLite finalizer fails."""

        try:
            self._finalize_quota(lease, usage, estimated_tokens)
        finally:
            await self._finalize_commercial(
                commercial_lease,
                usage,
                commercial_quantity,
            )

    async def _finalize_commercial(
        self,
        lease: Any,
        usage: Any,
        fallback_quantity: int,
    ) -> None:
        if lease is None:
            return
        from deeptutor.commercial.metering import (
            CommercialUsageUnavailable,
            measurement_from_usage,
        )

        from .adapters import EmbeddingProviderError

        measurement = measurement_from_usage(
            usage,
            fallback_quantity=fallback_quantity,
            meter="embedding_tokens",
        )
        try:
            await lease.finalize(
                measurement.quantity,
                usage_units=measurement.units,
                is_estimated=measurement.is_estimated,
            )
        except CommercialUsageUnavailable as exc:
            raise EmbeddingProviderError(
                str(exc),
                status=503,
                model=self.config.model,
                provider="deeptutor",
            ) from exc

    def supports_multimodal_contents(self) -> bool:
        """Return whether the configured adapter/model accepts multimodal contents."""
        try:
            info = self.adapter.get_model_info()
            if "multimodal" in info:
                return bool(info.get("multimodal"))
        except Exception:
            pass

        spec = EMBEDDING_PROVIDERS.get(self.config.binding)
        return bool(spec and spec.multimodal)

    async def embed_contents(
        self,
        contents: List[Dict[str, Any]],
        *,
        progress_callback=None,
    ) -> List[List[float]]:
        """Embed provider-agnostic multimodal content items.

        ``contents`` uses the same simple contract as ``EmbeddingRequest``:
        ``[{"text": "..."}, {"image": "data:...|url"}, {"video": "..."}]``.
        """
        if not contents:
            return []
        if not self.supports_multimodal_contents():
            raise ValueError(
                "Configured embedding provider/model does not support multimodal contents."
            )

        spec = EMBEDDING_PROVIDERS.get(self.config.binding)
        provider_max = spec.max_batch_items if spec else 256
        batch_size = max(1, min(self.config.batch_size, provider_max))
        all_embeddings: List[List[float]] = []
        total_batches = (len(contents) + batch_size - 1) // batch_size

        for i, start in enumerate(range(0, len(contents), batch_size)):
            batch = contents[start : start + batch_size]
            request = EmbeddingRequest(
                texts=[],
                model=self.config.model,
                dimensions=self.config.dim or None,
                contents=batch,
                enable_fusion=False,
            )
            await self._wait_for_request_slot()
            response = await self.adapter.embed(request)
            validated = validate_embedding_batch(
                response.embeddings,
                expected_count=len(batch),
                binding=self.config.binding,
                model=self.config.model,
                batch_index=i + 1,
                total_batches=total_batches,
                start_index=start,
            )
            all_embeddings.extend(validated)

            if progress_callback:
                try:
                    progress_callback(i + 1, total_batches)
                except Exception as exc:
                    # Same contract as embed(): log and continue, so a broken
                    # progress sink cannot stall multimodal indexing invisibly.
                    self.logger.warning(
                        f"Embedding progress callback failed "
                        f"(batch {i + 1}/{total_batches}): {exc}",
                        exc_info=True,
                    )

        return all_embeddings

    def embed_sync(self, texts: List[str]) -> List[List[float]]:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.embed(texts))

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, self.embed(texts))
            return future.result()


_client: Optional[EmbeddingClient] = None


def get_embedding_client(config: Optional[EmbeddingConfig] = None) -> EmbeddingClient:
    global _client
    resolved_config = config or get_embedding_config()
    client = _client
    if client is None or client.config != resolved_config:
        client = EmbeddingClient(resolved_config)
        _client = client
    # Each caller retains its own client even if another worker changes the cache.
    return client


def reset_embedding_client() -> None:
    global _client
    _client = None
