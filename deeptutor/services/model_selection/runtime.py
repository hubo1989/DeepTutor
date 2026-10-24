"""Runtime helpers for request-scoped model selection."""

from __future__ import annotations

from contextvars import Token
from typing import Any, TypeAlias

from deeptutor.multi_user.execution_source import (
    ExecutionSource,
    reset_execution_source,
    set_execution_source,
)
from deeptutor.services.config.provider_runtime import ResolvedLLMConfig, resolve_llm_runtime_config
from deeptutor.services.llm import config as llm_config_module
from deeptutor.services.llm.config import LLMConfig

LLMSelectionScopeToken: TypeAlias = tuple[
    Token[LLMConfig | None],
    Token[ExecutionSource | None],
]


def llm_config_from_resolved(resolved: ResolvedLLMConfig) -> LLMConfig:
    """Convert provider-runtime output into the LLM service config shape."""
    return LLMConfig(
        model=resolved.model,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        effective_url=resolved.effective_url,
        binding=resolved.binding,
        provider_name=resolved.provider_name,
        provider_mode=resolved.provider_mode,
        api_version=resolved.api_version,
        extra_headers=resolved.extra_headers,
        wire_api=resolved.wire_api,
        api_format=resolved.api_format,
        reasoning_effort=resolved.reasoning_effort,
        context_window=resolved.context_window,
        source=resolved.source,
    )


def resolve_llm_config_for_selection(selection: Any) -> LLMConfig:
    """Resolve the LLM config for a chat/session selection reference."""
    if selection is None:
        return llm_config_module.get_llm_config()
    return llm_config_from_resolved(resolve_llm_runtime_config(llm_selection=selection))


def activate_llm_selection(selection: Any) -> tuple[LLMConfig, LLMSelectionScopeToken]:
    """Resolve and install the matching model config and execution source."""
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.multi_user.model_access import default_llm_selection
    from deeptutor.services.model_selection import LLMSelection

    normalized = LLMSelection.from_payload(selection)
    if normalized is None and not get_current_user().is_admin:
        normalized = LLMSelection.from_payload(default_llm_selection())
    config = resolve_llm_config_for_selection(normalized)
    source = ExecutionSource(
        source=config.source,
        service="llm",
        user_id=get_current_user().id,
        profile_id=normalized.profile_id if normalized and config.source == "byok" else None,
        profile_generation=normalized.generation
        if normalized and config.source == "byok"
        else None,
        usage_origin=config.source,
    )
    config_token = llm_config_module.set_scoped_llm_config(config)
    source_token = set_execution_source(source)
    return config, (config_token, source_token)


def reset_llm_selection(token: LLMSelectionScopeToken | Token[LLMConfig | None] | None) -> None:
    """Restore the exact pair, accepting legacy config-only tokens as well."""
    if token is None:
        return
    if isinstance(token, Token):
        llm_config_module.reset_scoped_llm_config(token)
        return
    config_token, source_token = token
    llm_config_module.reset_scoped_llm_config(config_token)
    reset_execution_source(source_token)


__all__ = [
    "activate_llm_selection",
    "llm_config_from_resolved",
    "reset_llm_selection",
    "resolve_llm_config_for_selection",
]
