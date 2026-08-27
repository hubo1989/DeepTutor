"""Tests for provider-backed execution in llm.factory."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deeptutor.services.llm.config import LLMConfig
from deeptutor.services.llm.factory import (
    _commercial_factory_request_tokens,
    _reserve_factory_quota,
    complete,
    stream,
)
from deeptutor.services.llm.provider_core.base import LLMResponse


class _FakeProvider:
    def __init__(
        self,
        *,
        complete_response: LLMResponse | None = None,
        stream_response: LLMResponse | None = None,
        stream_chunk: str = "chunk",
        reasoning_chunk: str = "",
    ) -> None:
        self.complete_kwargs: dict[str, Any] = {}
        self.stream_kwargs: dict[str, Any] = {}
        self.complete_response = complete_response or LLMResponse(content="ok")
        self.stream_response = stream_response or LLMResponse(content=stream_chunk)
        self.stream_chunk = stream_chunk
        self.reasoning_chunk = reasoning_chunk

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.complete_kwargs = kwargs
        return self.complete_response

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.stream_kwargs = kwargs
        on_reasoning_delta = kwargs.get("on_reasoning_delta")
        if on_reasoning_delta is not None and self.reasoning_chunk:
            await on_reasoning_delta(self.reasoning_chunk)
        on_content_delta = kwargs.get("on_content_delta")
        if on_content_delta is not None:
            await on_content_delta(self.stream_chunk)
        return self.stream_response


class _CommercialLeaseProbe:
    def __init__(self) -> None:
        self.finalized: list[dict[str, Any]] = []
        self.released = 0

    async def finalize(self, quantity: int, **kwargs: Any) -> None:
        self.finalized.append({"quantity": quantity, **kwargs})

    async def release(self) -> None:
        self.released += 1


class _FailingQuotaLease:
    def __init__(self) -> None:
        self.released = 0

    def finalize(self, _quantity: int) -> None:
        raise RuntimeError("quota telemetry unavailable")

    def release(self) -> None:
        self.released += 1


def _make_cfg(**overrides: Any) -> LLMConfig:
    defaults = dict(
        model="gpt-4o-mini",
        api_key="test-key",
        base_url="https://api.example.com/v1",
        effective_url="https://api.example.com/v1",
        binding="openai",
        provider_name="openai",
        provider_mode="standard",
        extra_headers={},
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def test_factory_does_not_reserve_platform_quota_for_byok() -> None:
    assert _reserve_factory_quota(_make_cfg(source="byok"), [], max_tokens=128) == (None, 0)


def test_commercial_request_bound_includes_tool_and_response_schemas() -> None:
    messages = [{"role": "user", "content": "hi"}]
    baseline = _commercial_factory_request_tokens(messages, max_tokens=64)
    with_schemas = _commercial_factory_request_tokens(
        messages,
        max_tokens=64,
        request_options={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "parameters": {"description": "x" * 2_000},
                    },
                }
            ],
            "response_format": {"schema": {"description": "y" * 2_000}},
        },
    )

    assert with_schemas > baseline + 4_000


@pytest.mark.asyncio
async def test_complete_merges_config_and_caller_extra_headers(monkeypatch) -> None:
    cfg = _make_cfg(extra_headers={"X-Config": "from-config"})
    provider = _FakeProvider()
    captured_config: dict[str, Any] = {}

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)

    def _fake_get_runtime_provider(config: LLMConfig):
        captured_config["config"] = config
        return provider

    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider", _fake_get_runtime_provider
    )

    result = await complete("hello", extra_headers={"X-Caller": "from-caller"})

    assert result == "ok"
    merged = captured_config["config"].extra_headers
    assert merged == {"X-Config": "from-config", "X-Caller": "from-caller"}


@pytest.mark.asyncio
async def test_complete_honors_explicit_image_fallback_override(monkeypatch) -> None:
    cfg = _make_cfg(model="unknown-model", binding="custom", provider_name="custom")
    provider = _FakeProvider()

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )

    assert await complete("hello", allow_image_fallback=False) == "ok"
    assert provider.complete_kwargs["allow_image_fallback"] is False


@pytest.mark.asyncio
async def test_stream_merges_config_and_caller_extra_headers(monkeypatch) -> None:
    cfg = _make_cfg(extra_headers={"X-Config": "cfg"})
    provider = _FakeProvider(stream_chunk="A")
    captured_config: dict[str, Any] = {}

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)

    def _fake_get_runtime_provider(config: LLMConfig):
        captured_config["config"] = config
        return provider

    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider", _fake_get_runtime_provider
    )

    chunks = []
    async for chunk in stream("hello", extra_headers={"X-Caller": "clr"}):
        chunks.append(chunk)

    assert chunks == ["A"]
    merged = captured_config["config"].extra_headers
    assert merged == {"X-Config": "cfg", "X-Caller": "clr"}


@pytest.mark.asyncio
async def test_complete_error_response_still_finalizes_commercial_usage(monkeypatch) -> None:
    cfg = _make_cfg()
    provider = _FakeProvider(
        complete_response=LLMResponse(
            content="provider rejected final response",
            finish_reason="error",
            usage={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
        )
    )
    lease = _CommercialLeaseProbe()
    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._reserve_factory_commercial",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=(lease, 100)),
    )

    with pytest.raises(Exception, match="provider rejected final response"):
        await complete("hello")

    assert lease.released == 0
    assert lease.finalized == [
        {
            "quantity": 6,
            "usage_units": {
                "input_tokens": 5,
                "output_tokens": 1,
                "total_tokens": 6,
            },
            "is_estimated": False,
        }
    ]


@pytest.mark.asyncio
async def test_complete_settles_commercial_when_legacy_quota_finalize_fails(monkeypatch) -> None:
    cfg = _make_cfg()
    provider = _FakeProvider(
        complete_response=LLMResponse(
            content="ok",
            usage={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        )
    )
    commercial_lease = _CommercialLeaseProbe()
    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider", lambda _config: provider
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._reserve_factory_commercial",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=(commercial_lease, 100)),
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._reserve_factory_quota",
        lambda *_args, **_kwargs: (_FailingQuotaLease(), 100),
    )

    with pytest.raises(RuntimeError, match="quota telemetry unavailable"):
        await complete("hello")

    assert commercial_lease.released == 0
    assert commercial_lease.finalized[0]["quantity"] == 6


@pytest.mark.asyncio
async def test_stream_reports_finalize_failure_without_hanging_or_stranding_commercial(
    monkeypatch,
) -> None:
    cfg = _make_cfg()
    commercial_lease = _CommercialLeaseProbe()
    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: _FakeProvider(
            stream_chunk="A",
            stream_response=LLMResponse(
                content="A",
                usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            ),
        ),
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._reserve_factory_commercial",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=(commercial_lease, 100)),
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._reserve_factory_quota",
        lambda *_args, **_kwargs: (_FailingQuotaLease(), 100),
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._start_byok_factory_usage",
        lambda *_args, **_kwargs: (None, 0),
    )

    generator = stream("hello", stream_coalesce_seconds=0)
    assert await asyncio.wait_for(generator.__anext__(), timeout=1) == "A"
    with pytest.raises(Exception, match="quota telemetry unavailable"):
        await asyncio.wait_for(generator.__anext__(), timeout=1)
    await generator.aclose()

    assert commercial_lease.released == 0
    assert commercial_lease.finalized[0]["quantity"] == 5


@pytest.mark.asyncio
async def test_cancelled_factory_stream_with_output_is_still_metered(monkeypatch) -> None:
    cfg = _make_cfg()
    lease = _CommercialLeaseProbe()
    hold = asyncio.Event()

    class BlockingProvider(_FakeProvider):
        async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
            await kwargs["on_content_delta"]("partial")
            await hold.wait()
            return LLMResponse(content="partial")

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: BlockingProvider(),
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._reserve_factory_commercial",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=(lease, 100)),
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._reserve_factory_quota",
        lambda *_args, **_kwargs: (None, 0),
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.factory._start_byok_factory_usage",
        lambda *_args, **_kwargs: (None, 0),
    )

    generator = stream("hello", stream_coalesce_seconds=0)
    assert await generator.__anext__() == "partial"
    await generator.aclose()

    assert lease.released == 0
    assert lease.finalized == [
        {
            "quantity": 100,
            "usage_units": {"llm_tokens": 100},
            "is_estimated": True,
        }
    ]


@pytest.mark.asyncio
async def test_explicit_call_inherits_matching_profile_headers_and_reasoning(
    monkeypatch,
) -> None:
    cfg = _make_cfg(
        extra_headers={"User-Agent": "DeepTutor-Test"},
        reasoning_effort="minimal",
    )
    provider = _FakeProvider()
    captured_config: dict[str, LLMConfig] = {}

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)

    def _fake_get_runtime_provider(config: LLMConfig):
        captured_config["config"] = config
        return provider

    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider", _fake_get_runtime_provider
    )

    result = await complete(
        "hello",
        model=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        binding=cfg.binding,
    )

    assert result == "ok"
    assert captured_config["config"].extra_headers == {"User-Agent": "DeepTutor-Test"}
    assert captured_config["config"].reasoning_effort == "minimal"
    assert provider.complete_kwargs["reasoning_effort"] == "minimal"


@pytest.mark.asyncio
async def test_explicit_byok_source_overrides_platform_active_config(monkeypatch) -> None:
    """Factory calls retain BYOK accounting when context propagation is absent."""
    cfg = _make_cfg(source="platform")
    provider = _FakeProvider()
    captured_config: dict[str, LLMConfig] = {}

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)

    def _fake_get_runtime_provider(config: LLMConfig):
        captured_config["config"] = config
        return provider

    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider", _fake_get_runtime_provider
    )

    assert (
        await complete(
            "hello",
            model=cfg.model,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            binding=cfg.binding,
            source="byok",
        )
        == "ok"
    )
    assert captured_config["config"].source == "byok"


@pytest.mark.asyncio
async def test_stream_does_not_replay_reasoning_as_final_content(monkeypatch) -> None:
    cfg = _make_cfg()
    provider = _FakeProvider(
        stream_chunk="",
        reasoning_chunk="scratchpad",
        stream_response=LLMResponse(
            content="scratchpad",
            reasoning_content="scratchpad",
        ),
    )

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )

    chunks = []
    async for chunk in stream("hello"):
        chunks.append(chunk)

    assert chunks == ["<think>", "scratchpad", "</think>"]


@pytest.mark.asyncio
async def test_complete_injects_openai_image_parts(monkeypatch) -> None:
    cfg = _make_cfg(model="gpt-4o-mini", binding="openai", provider_name="openai")
    provider = _FakeProvider()

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )

    result = await complete(
        "ignored",
        messages=[{"role": "user", "content": "hi"}],
        image_data="abc123",
    )

    assert result == "ok"
    content = provider.complete_kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,abc123")


@pytest.mark.asyncio
async def test_complete_injects_anthropic_image_parts(monkeypatch) -> None:
    cfg = _make_cfg(
        model="claude-sonnet-4-20250514",
        binding="anthropic",
        provider_name="anthropic",
    )
    provider = _FakeProvider()

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )

    result = await complete(
        "ignored",
        messages=[{"role": "user", "content": "hi"}],
        image_data="abc123",
    )

    assert result == "ok"
    content = provider.complete_kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[1]["type"] == "image"
    assert content[1]["source"]["type"] == "base64"


@pytest.mark.asyncio
async def test_complete_injects_custom_anthropic_image_parts(monkeypatch) -> None:
    cfg = _make_cfg(
        model="claude-sonnet-4-20250514",
        binding="custom_anthropic",
        provider_name="custom_anthropic",
    )
    provider = _FakeProvider()

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )

    result = await complete(
        "ignored",
        messages=[{"role": "user", "content": "hi"}],
        image_data="abc123",
    )

    assert result == "ok"
    content = provider.complete_kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[1]["type"] == "image"
    assert content[1]["source"]["type"] == "base64"


@pytest.mark.asyncio
async def test_complete_strips_unsupported_response_format(monkeypatch) -> None:
    cfg = _make_cfg(
        model="deepseek-reasoner",
        binding="deepseek",
        provider_name="deepseek",
    )
    provider = _FakeProvider()

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )

    result = await complete(
        "hello",
        response_format={"type": "json_object"},
    )

    assert result == "ok"
    assert "response_format" not in provider.complete_kwargs


@pytest.mark.asyncio
async def test_complete_passes_retry_delays(monkeypatch) -> None:
    cfg = _make_cfg()
    provider = _FakeProvider()

    monkeypatch.setattr("deeptutor.services.llm.factory.get_llm_config", lambda: cfg)
    monkeypatch.setattr(
        "deeptutor.services.llm.factory.get_runtime_provider",
        lambda _config: provider,
    )

    await complete("hello", max_retries=3, retry_delay=0.5, exponential_backoff=True)

    assert provider.complete_kwargs["retry_delays"] == (0.5, 1.0, 2.0)
