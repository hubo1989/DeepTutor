"""OpenAI-compatible client factory and completion kwargs.

Lifted from chat's pipeline so any capability that wants a streaming LLM call
with tools can construct the same client + kwargs without re-implementing
provider gating, Azure detection, SSL bypass, or per-model token caps.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
import threading
from types import SimpleNamespace
from typing import Any, Literal

import httpx
from openai import AsyncAzureOpenAI, AsyncOpenAI

from deeptutor.services.config import load_system_settings
from deeptutor.services.llm import get_token_limit_kwargs, supports_tools
from deeptutor.services.llm.exceptions import (
    LLMProviderError,
    LLMRateLimitError,
)
from deeptutor.services.llm.reasoning_params import (
    build_openai_compatible_reasoning_kwargs,
)
from deeptutor.services.provider_registry import find_by_name

# Providers that don't reliably support OpenAI function-calling. The loop
# still runs without tool schemas — the model just produces prose.
_NATIVE_TOOL_BLOCKED_BINDINGS: frozenset[str] = frozenset(
    {"anthropic", "claude", "ollama", "lm_studio", "vllm", "llama_cpp"}
)

# Native provider adapters whose backends speak OpenAI-style function calling
# end to end (schema serialization + tool-call parsing). Backends validated
# here get tools attached regardless of the binding blocklist above.
# Invariant: must be a subset of _NATIVE_ADAPTER_BUILDERS — every tool-gated
# backend needs an adapter branch, or tool schemas would be attached to a plain
# AsyncOpenAI client pointed at a non-OpenAI wire format. github_copilot is
# adapter-routed but deliberately excluded from this set.
_NATIVE_TOOL_BACKENDS: frozenset[str] = frozenset({"anthropic", "openai_codex"})
_AGENTIC_CLIENT_POOL_MAXSIZE = 2
_agentic_client_pool: "OrderedDict[tuple[Any, ...], Any]" = OrderedDict()
_agentic_client_pool_lock = threading.RLock()


@dataclass(frozen=True)
class LLMClientConfig:
    """Provider-neutral handle for constructing an OpenAI-compatible client."""

    binding: str
    model: str | None
    api_key: str | None
    base_url: str | None
    api_version: str | None = None
    extra_headers: dict[str, str] | None = None
    reasoning_effort: str | None = None
    source: Literal["platform", "byok"] = "platform"


def _client_cache_key(
    config: LLMClientConfig,
    loop: asyncio.AbstractEventLoop,
    disable_ssl_verify: bool,
) -> tuple[Any, ...]:
    secret = hashlib.sha256((config.api_key or "").encode("utf-8")).hexdigest()[:16]
    headers = json.dumps(config.extra_headers or {}, sort_keys=True, separators=(",", ":"))
    return (
        loop,
        config.binding,
        config.model or "",
        secret,
        config.base_url or "",
        config.api_version or "",
        headers,
        disable_ssl_verify,
    )


def _build_openai_client(config: LLMClientConfig, *, disable_ssl_verify: bool) -> Any:
    default_headers = config.extra_headers or None
    spec = find_by_name(config.binding)
    if spec:
        native_adapter = _build_native_provider_adapter(config, spec)
        if native_adapter is not None:
            return _wrap_token_quota(
                native_adapter,
                source=config.source,
                provider=config.binding,
                model=config.model,
            )

    http_client = None
    if disable_ssl_verify:
        http_client = httpx.AsyncClient(verify=False)  # nosec B501
    if config.binding == "azure_openai" or (config.binding == "openai" and config.api_version):
        client = AsyncAzureOpenAI(
            api_key=config.api_key or "sk-no-key-required",
            azure_endpoint=config.base_url,
            api_version=config.api_version,
            http_client=http_client,
            default_headers=default_headers,
        )
        return _wrap_token_quota(
            client,
            source=config.source,
            provider=config.binding,
            model=config.model,
        )
    return _wrap_token_quota(
        AsyncOpenAI(
            api_key=config.api_key or "sk-no-key-required",
            base_url=config.base_url or None,
            http_client=http_client,
            default_headers=default_headers,
        ),
        source=config.source,
        provider=config.binding,
        model=config.model,
    )


def _estimate_tokens(value: Any) -> int:
    """Conservative, provider-neutral estimate used for pre-call reservation."""
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(value)
    return max(1, math.ceil(len(serialized) / 3.5))


def _request_token_estimate(kwargs: dict[str, Any]) -> tuple[int, int, int]:
    prompt_tokens = _estimate_tokens(
        {
            "messages": kwargs.get("messages") or [],
            "tools": kwargs.get("tools") or [],
        }
    )
    raw_output = kwargs.get("max_completion_tokens", kwargs.get("max_tokens", 4096))
    try:
        output_tokens = max(1, int(raw_output or 4096))
    except (TypeError, ValueError):
        output_tokens = 4096
    return prompt_tokens + output_tokens, prompt_tokens, output_tokens


def _request_commercial_token_bound(kwargs: dict[str, Any], output_tokens: int) -> int:
    """Bound prompt tokenizer pieces by serialized UTF-8 bytes."""

    value = {
        "messages": kwargs.get("messages") or [],
        "tools": kwargs.get("tools") or [],
    }
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(value)
    return max(1, len(serialized.encode("utf-8"))) + output_tokens


def _usage_total(value: Any) -> int:
    usage = value
    if usage is None:
        return 0
    if isinstance(usage, dict):
        try:
            return max(0, int(usage.get("total_tokens") or 0))
        except (TypeError, ValueError):
            return 0
    try:
        return max(0, int(getattr(usage, "total_tokens", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _stream_chunk_text(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    return str(getattr(delta, "content", "") or "") if delta is not None else ""


def _wrap_token_quota(
    client: Any,
    *,
    source: str = "platform",
    provider: str | None = None,
    model: str | None = None,
) -> Any:
    """Wrap generated clients with platform quota or BYOK safety accounting."""
    from deeptutor.commercial.metering import commercial_usage_required
    from deeptutor.multi_user.token_quota import current_user_quota_policy

    # BYOK calls still need the request-rate/token safety gate; only the
    # platform path may skip wrapping when no user quota is configured.  The
    # source is carried by LLMClientConfig so this remains correct when the
    # request ContextVar does not survive a thread/async boundary.
    normalized_source: Literal["platform", "byok"] = "byok" if source == "byok" else "platform"
    if (
        normalized_source == "platform"
        and current_user_quota_policy() is None
        and not commercial_usage_required(source=normalized_source)
    ):
        return client
    return _TokenQuotaClient(
        client,
        source=normalized_source,
        provider=provider,
        model=model,
    )


class _TokenQuotaClient:
    def __init__(
        self,
        client: Any,
        *,
        source: Literal["platform", "byok"] = "platform",
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self._client = client
        self.chat = SimpleNamespace(
            completions=_TokenQuotaCompletions(
                client.chat.completions,
                source=source,
                provider=provider,
                model=model,
            ),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _TokenQuotaCompletions:
    def __init__(
        self,
        completions: Any,
        *,
        source: Literal["platform", "byok"] = "platform",
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self._completions = completions
        self._source = source
        self._provider = provider
        self._model = model

    async def create(self, **kwargs: Any) -> Any:
        # DeepTutor-only accounting metadata must never reach the provider.
        # Callers that can retry the same logical provider operation should
        # reuse this value so PostgreSQL can return the durable reservation
        # instead of booking a duplicate.
        commercial_request_id = kwargs.pop("_commercial_request_id", None)
        requested, prompt_estimate, output_estimate = _request_token_estimate(kwargs)
        commercial_requested = _request_commercial_token_bound(kwargs, output_estimate)
        if self._source == "byok":
            from deeptutor.commercial.metering import (
                CommercialUsageLimitExceeded,
                CommercialUsageUnavailable,
                reserve_commercial_usage,
            )
            from deeptutor.multi_user.byok_usage import (
                ByokUsageLimitExceeded,
                ByokUsageUnavailable,
                start_byok_usage,
            )
            from deeptutor.multi_user.execution_source import get_execution_source

            execution_source = get_execution_source()
            if execution_source is None:
                from deeptutor.multi_user.context import get_current_user

                user_id = get_current_user().id
                profile_id = "unknown"
            else:
                user_id = execution_source.user_id
                profile_id = execution_source.profile_id or "unknown"
            try:
                # BYOK tokens remain user-funded, but an expired hosted
                # account must not bypass the subscription gate by selecting
                # a private key.
                await reserve_commercial_usage(
                    meter="llm_tokens",
                    quantity=commercial_requested,
                    source="byok",
                    provider=self._provider or "openai-compatible",
                    model=str(kwargs.get("model") or self._model or "unknown"),
                    request_id=commercial_request_id,
                )
            except CommercialUsageLimitExceeded as exc:
                raise LLMRateLimitError(str(exc), provider="deeptutor") from exc
            except CommercialUsageUnavailable as exc:
                raise LLMProviderError(str(exc), provider="deeptutor") from exc
            try:
                byok_lease = start_byok_usage(
                    service="llm",
                    user_id=user_id,
                    profile_id=profile_id,
                    provider="openai-compatible",
                    model=str(kwargs.get("model") or "unknown"),
                    estimated_units=requested,
                )
            except ByokUsageLimitExceeded as exc:
                raise LLMRateLimitError(str(exc), provider="deeptutor") from exc
            except ByokUsageUnavailable as exc:
                raise LLMProviderError(str(exc), provider="deeptutor") from exc
            try:
                response = await self._completions.create(**kwargs)
            except BaseException:
                byok_lease.release()
                raise
            if not kwargs.get("stream"):
                actual = _usage_total(getattr(response, "usage", None)) or requested
                byok_lease.finalize(actual)
                return response
            return _TokenQuotaStream(
                response,
                lease=None,
                byok_lease=byok_lease,
                commercial_lease=None,
                requested_tokens=requested,
                commercial_requested_tokens=0,
                prompt_estimate=prompt_estimate,
                output_estimate=output_estimate,
            )
        from deeptutor.commercial.metering import (
            CommercialUsageLimitExceeded,
            CommercialUsageUnavailable,
            measurement_from_usage,
            reserve_commercial_usage,
        )
        from deeptutor.multi_user.token_quota import (
            TokenQuotaExceeded,
            TokenQuotaUnavailable,
            reserve_current_user_tokens,
        )

        provider = self._provider or "openai-compatible"
        model = str(kwargs.get("model") or self._model or "unknown")
        try:
            commercial_lease = await reserve_commercial_usage(
                meter="llm_tokens",
                quantity=commercial_requested,
                source="platform",
                provider=provider,
                model=model,
                request_id=commercial_request_id,
            )
        except CommercialUsageLimitExceeded as exc:
            raise LLMRateLimitError(str(exc), provider="deeptutor") from exc
        except CommercialUsageUnavailable as exc:
            raise LLMProviderError(str(exc), provider="deeptutor") from exc

        try:
            lease = reserve_current_user_tokens(
                requested_tokens=requested,
                prompt_tokens_estimate=prompt_estimate,
                output_tokens_estimate=output_estimate,
            )
        except TokenQuotaExceeded as exc:
            if commercial_lease is not None:
                await commercial_lease.release()
            raise LLMRateLimitError(str(exc), provider="deeptutor") from exc
        except TokenQuotaUnavailable as exc:
            if commercial_lease is not None:
                await commercial_lease.release()
            raise LLMProviderError(str(exc), provider="deeptutor") from exc

        try:
            response = await self._completions.create(**kwargs)
        except BaseException:
            try:
                if lease is not None:
                    lease.release()
            finally:
                if commercial_lease is not None:
                    await commercial_lease.release()
            raise

        if not kwargs.get("stream"):
            usage = getattr(response, "usage", None)
            try:
                if lease is not None:
                    actual = _usage_total(usage)
                    if actual <= 0:
                        # Providers that omit usage are charged the reservation
                        # upper bound; otherwise a user could bypass quota by
                        # selecting a backend that does not report token counts.
                        actual = requested
                    lease.finalize(actual)
            finally:
                if commercial_lease is not None:
                    measurement = measurement_from_usage(
                        usage,
                        fallback_quantity=commercial_requested,
                        meter="llm_tokens",
                    )
                    try:
                        await commercial_lease.finalize(
                            measurement.quantity,
                            usage_units=measurement.units,
                            is_estimated=measurement.is_estimated,
                        )
                    except CommercialUsageUnavailable as exc:
                        raise LLMProviderError(str(exc), provider="deeptutor") from exc
            return response
        if lease is None and commercial_lease is None:
            return response
        return _TokenQuotaStream(
            response,
            lease=lease,
            commercial_lease=commercial_lease,
            requested_tokens=requested,
            commercial_requested_tokens=commercial_requested,
            prompt_estimate=prompt_estimate,
            output_estimate=output_estimate,
        )


class _TokenQuotaStream:
    def __init__(
        self,
        stream: Any,
        *,
        lease: Any,
        byok_lease: Any = None,
        commercial_lease: Any = None,
        requested_tokens: int,
        commercial_requested_tokens: int = 0,
        prompt_estimate: int,
        output_estimate: int,
    ) -> None:
        self._stream = stream
        self._lease = lease
        self._byok_lease = byok_lease
        self._commercial_lease = commercial_lease
        self._requested_tokens = requested_tokens
        self._commercial_requested_tokens = commercial_requested_tokens
        self._prompt_estimate = prompt_estimate
        self._output_estimate = output_estimate
        self._usage_total = 0
        self._usage_measurement: Any = None
        self._output_chars = 0
        self._finalized = False

    def __aiter__(self) -> "_TokenQuotaStream":
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            await self._finalize(status="success")
            raise
        except BaseException:
            await self._finalize(status="failed")
            raise
        usage = getattr(chunk, "usage", None)
        self._usage_total = max(self._usage_total, _usage_total(usage))
        if usage is not None:
            from deeptutor.commercial.metering import measurement_from_usage

            measurement = measurement_from_usage(
                usage,
                fallback_quantity=(self._commercial_requested_tokens or self._requested_tokens),
                meter="llm_tokens",
            )
            if not measurement.is_estimated:
                self._usage_measurement = measurement
        self._output_chars += len(_stream_chunk_text(chunk))
        return chunk

    async def _finalize(self, *, status: str = "success") -> None:
        if self._finalized:
            return
        self._finalized = True
        actual = self._usage_total
        if actual <= 0:
            estimated_output = max(
                math.ceil(self._output_chars / 3.5),
                min(self._output_estimate, 256) if self._output_chars else 0,
            )
            actual = max(self._requested_tokens, self._prompt_estimate + estimated_output)
        try:
            if self._lease is not None:
                self._lease.finalize(actual)
            if self._byok_lease is not None:
                self._byok_lease.finalize(actual, status=status)
        finally:
            if self._commercial_lease is not None:
                from deeptutor.commercial.metering import CommercialMeteringError

                try:
                    measurement = self._usage_measurement
                    has_provider_usage = measurement is not None or self._usage_total > 0
                    has_stream_output = self._output_chars > 0
                    if status != "success" and not has_provider_usage and not has_stream_output:
                        await self._commercial_lease.release()
                    else:
                        commercial_actual = (
                            measurement.quantity
                            if measurement is not None
                            else min(
                                self._commercial_requested_tokens or actual,
                                max(1, actual),
                            )
                        )
                        await self._commercial_lease.finalize(
                            commercial_actual,
                            usage_units=(
                                measurement.units
                                if measurement is not None
                                else {"llm_tokens": commercial_actual}
                            ),
                            is_estimated=measurement is None,
                        )
                except CommercialMeteringError as exc:
                    raise LLMProviderError(str(exc), provider="deeptutor") from exc

    async def close(self) -> None:
        await self._finalize(status="cancelled")
        close = getattr(self._stream, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result
            return
        aclose = getattr(self._stream, "aclose", None)
        if callable(aclose):
            result = aclose()
            if hasattr(result, "__await__"):
                await result


async def _close_client(client: Any) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _schedule_client_close(client: Any, loop: asyncio.AbstractEventLoop) -> None:
    async def _close() -> None:
        with contextlib.suppress(Exception):
            await _close_client(client)

    loop.create_task(_close())


def build_openai_client(config: LLMClientConfig) -> Any:
    """Return a bounded, event-loop-local OpenAI-compatible client.

    The chat, research and question pipelines build this handle per turn.  The
    handle itself owns an HTTP connection pool, so reusing it is both faster
    and prevents a new allocator/socket high-water mark on every turn.
    """
    disable_ssl_verify = bool(load_system_settings()["disable_ssl_verify"])
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _build_openai_client(config, disable_ssl_verify=disable_ssl_verify)

    key = _client_cache_key(config, loop, disable_ssl_verify)
    with _agentic_client_pool_lock:
        cached = _agentic_client_pool.get(key)
        if cached is not None:
            _agentic_client_pool.move_to_end(key)
            return cached
        client = _build_openai_client(config, disable_ssl_verify=disable_ssl_verify)
        _agentic_client_pool[key] = client
        _agentic_client_pool.move_to_end(key)
        while len(_agentic_client_pool) > _AGENTIC_CLIENT_POOL_MAXSIZE:
            _, evicted = _agentic_client_pool.popitem(last=False)
            _schedule_client_close(evicted, loop)
        return client


async def close_agentic_client_pool() -> None:
    with _agentic_client_pool_lock:
        clients = list(_agentic_client_pool.values())
        _agentic_client_pool.clear()
    if clients:
        await asyncio.gather(*(_close_client(client) for client in clients), return_exceptions=True)


def reset_agentic_client_pool() -> None:
    with _agentic_client_pool_lock:
        clients = list(_agentic_client_pool.values())
        _agentic_client_pool.clear()
    if not clients:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        for client in clients:
            with contextlib.suppress(Exception):
                asyncio.run(_close_client(client))
        return
    for client in clients:
        _schedule_client_close(client, loop)


def agentic_client_pool_size() -> int:
    with _agentic_client_pool_lock:
        return len(_agentic_client_pool)


def _build_anthropic_adapter(config: LLMClientConfig, spec: Any) -> Any:
    from deeptutor.services.llm.provider_core import AnthropicProvider

    anthropic_provider = AnthropicProvider(
        api_key=config.api_key,
        api_base=config.base_url or spec.default_api_base or None,
        default_model=config.model or "claude-sonnet-4-20250514",
        extra_headers=config.extra_headers,
        supports_prompt_caching=spec.supports_prompt_caching,
    )
    return _ProviderOpenAIAdapter(anthropic_provider)


def _build_codex_adapter(config: LLMClientConfig, spec: Any) -> Any:
    from deeptutor.services.codex_auth.constants import CODEX_DEFAULT_MODEL_ID
    from deeptutor.services.llm.provider_core import OpenAICodexProvider

    oauth_provider = OpenAICodexProvider(
        default_model=config.model or CODEX_DEFAULT_MODEL_ID,
    )
    return _ProviderOpenAIAdapter(oauth_provider)


def _build_copilot_adapter(config: LLMClientConfig, spec: Any) -> Any:
    from deeptutor.services.llm.provider_core import GitHubCopilotProvider

    copilot_provider = GitHubCopilotProvider(
        default_model=config.model or "github-copilot/gpt-4.1",
    )
    return _ProviderOpenAIAdapter(copilot_provider)


_NATIVE_ADAPTER_BUILDERS: dict[str, Callable[[LLMClientConfig, Any], Any]] = {
    "anthropic": _build_anthropic_adapter,
    "openai_codex": _build_codex_adapter,
    "github_copilot": _build_copilot_adapter,
}


def _build_native_provider_adapter(config: LLMClientConfig, spec: Any) -> Any | None:
    builder = _NATIVE_ADAPTER_BUILDERS.get(spec.backend)
    return builder(config, spec) if builder else None


class _ProviderOpenAIAdapter:
    """OpenAI chat-completions facade backed by a native provider."""

    def __init__(self, provider: Any):
        self._provider = provider
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_completion))

    async def close(self) -> None:
        close = getattr(self._provider, "aclose", None)
        if callable(close):
            await close()

    async def _create_completion(self, **kwargs: Any) -> Any:
        stream = bool(kwargs.pop("stream", False))
        messages = kwargs.pop("messages", [])
        model = kwargs.pop("model", None)
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        temperature = kwargs.pop("temperature", 0.7)
        max_tokens = kwargs.pop("max_completion_tokens", None)
        if max_tokens is None:
            max_tokens = kwargs.pop("max_tokens", 4096)
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        kwargs.pop("stream_options", None)

        if stream:
            return _ProviderOpenAIStream(
                provider=self._provider,
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                tool_choice=tool_choice,
                extra_kwargs=kwargs,
            )

        response = await self._provider.chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
            **kwargs,
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=response.content or "",
                        tool_calls=[
                            _openai_tool_call(tool_call, index=index)
                            for index, tool_call in enumerate(response.tool_calls or [])
                        ],
                    ),
                    finish_reason=response.finish_reason or "stop",
                )
            ],
            usage=response.usage or None,
        )


class _ProviderOpenAIStream:
    def __init__(
        self,
        *,
        provider: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: Any,
        temperature: Any,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        extra_kwargs: dict[str, Any],
    ) -> None:
        self._provider = provider
        self._messages = messages
        self._tools = tools
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._tool_choice = tool_choice
        self._extra_kwargs = extra_kwargs
        self._queue: asyncio.Queue[Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._emitted_content = False

    def __aiter__(self) -> "_ProviderOpenAIStream":
        if self._queue is None:
            self._queue = asyncio.Queue()
            self._task = asyncio.create_task(self._run())
        return self

    async def __anext__(self) -> Any:
        if self._queue is None:
            self.__aiter__()
        assert self._queue is not None
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        assert self._queue is not None

        async def _on_content_delta(text: str) -> None:
            if text:
                self._emitted_content = True
                await self._queue.put(_openai_stream_chunk(content=text))

        try:
            response = await self._provider.chat_stream(
                messages=self._messages,
                tools=self._tools,
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                reasoning_effort=self._reasoning_effort,
                tool_choice=self._tool_choice,
                on_content_delta=_on_content_delta,
                **self._extra_kwargs,
            )
            if response.content and not self._emitted_content:
                await self._queue.put(_openai_stream_chunk(content=response.content))
            for index, tool_call in enumerate(response.tool_calls or []):
                await self._queue.put(_openai_stream_chunk(tool_call=tool_call, index=index))
            await self._queue.put(
                _openai_stream_chunk(
                    finish_reason=response.finish_reason or "stop",
                    usage=response.usage or None,
                )
            )
        except Exception as exc:
            await self._queue.put(exc)
        finally:
            await self._queue.put(None)


_AnthropicOpenAIAdapter = _ProviderOpenAIAdapter
_AnthropicOpenAIStream = _ProviderOpenAIStream


def _openai_tool_call(tool_call: Any, *, index: int) -> Any:
    function = SimpleNamespace(
        name=getattr(tool_call, "name", ""),
        arguments=json.dumps(getattr(tool_call, "arguments", {}) or {}, ensure_ascii=False),
    )
    return SimpleNamespace(
        index=index,
        id=getattr(tool_call, "id", ""),
        type="function",
        function=function,
    )


def _openai_stream_chunk(
    *,
    content: str | None = None,
    tool_call: Any | None = None,
    index: int = 0,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> Any:
    tool_calls = None
    if tool_call is not None:
        tool_calls = [_openai_tool_call(tool_call, index=index)]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def build_completion_kwargs(
    *,
    temperature: float,
    model: str | None,
    max_tokens: int,
    binding: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Compose temperature + per-model token-limit kwargs into one dict."""
    kwargs: dict[str, Any] = {"temperature": temperature}
    if model:
        kwargs.update(get_token_limit_kwargs(model, max_tokens))
    kwargs.update(
        build_provider_extra_kwargs(
            binding=binding,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    )
    return kwargs


def build_provider_extra_kwargs(
    *,
    binding: str | None,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """Return provider-specific kwargs for raw OpenAI-compatible agent calls.

    Agentic pipelines stream directly through ``AsyncOpenAI`` so tests can
    inject scripted clients. This helper mirrors the small provider-normalized
    subset that is required before those raw calls: reasoning effort and
    provider-specific thinking flags.
    """
    spec = find_by_name(binding)
    return build_openai_compatible_reasoning_kwargs(
        spec=spec,
        binding=binding,
        model=model,
        reasoning_effort=reasoning_effort,
    )


def can_use_native_tool_calling(*, binding: str, model: str | None) -> bool:
    """Whether the current provider supports OpenAI-style function calling.

    Resolution order:

    1. Native provider adapters backed by Anthropic or OpenAI Codex support tools.
    2. Local OpenAI-compatible servers (Ollama, vLLM, LM Studio, llama.cpp,
       Lemonade, OVMS, …) and anything in ``_NATIVE_TOOL_BLOCKED_BINDINGS`` are
       opted out — tool support there depends on the loaded model and is
       unreliable, so the loop falls back to prose.
    3. An explicit ``supports_tools`` capability (provider- or model-level) wins.
    4. Otherwise a registered *cloud* OpenAI-compatible provider is assumed
       tool-capable — function calling is part of that API contract, matching
       the catch-all ``custom`` provider. This keeps newly added cloud
       providers working without a dedicated capability entry, instead of
       silently disabling native tools (the gap that affected e.g. SiliconFlow,
       Gemini, Zhipu, Qianfan, NVIDIA NIM and the Volc/BytePlus coding plans).
       To opt a cloud provider out, add its binding to
       ``_NATIVE_TOOL_BLOCKED_BINDINGS``.
    """
    spec = find_by_name(binding)
    if spec and spec.backend in _NATIVE_TOOL_BACKENDS:
        return True
    if binding in _NATIVE_TOOL_BLOCKED_BINDINGS or (spec and spec.is_local):
        return False
    if supports_tools(binding, model):
        return True
    return bool(spec and spec.backend == "openai_compat")
