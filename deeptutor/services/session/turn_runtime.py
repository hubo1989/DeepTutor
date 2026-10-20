"""Compatibility facade for the decomposed v2 turn services."""

from __future__ import annotations

from . import _turn_runtime_shared as _shared
from .turns import (
    LearningTurnAdapter,
    SessionTitleService,
    TurnContextAssembler,
    TurnExecutor,
    TurnLifecycle,
    TurnRequestPreparer,
)


# Preserve direct imports of normalization helpers during the v2 transition.
# PEP 562 forwards private helpers without copying the 1,200-line value layer
# back into this compatibility module.
def __getattr__(name: str):
    try:
        return getattr(_shared, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


class TurnRuntimeManager(
    TurnRequestPreparer,
    TurnContextAssembler,
    LearningTurnAdapter,
    TurnExecutor,
    TurnLifecycle,
    SessionTitleService,
):
    """Backward-compatible composition of the focused v2 turn services."""

    async def start_turn(
        self,
        payload: dict[str, Any],
        *,
        replace_assistant_message_id: int | str | None = None,
        commercial_lease: Any = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply hosted-plan gates before any session, message, or file write."""
        from deeptutor.commercial.concurrency import acquire_commercial_turn_lease
        from deeptutor.commercial.entitlement_context import (
            CommercialAccessDenied,
            require_capability,
        )

        # Validate the public capability config before any reservation so an
        # invalid config cannot consume shared owner capacity.
        capability = str(payload.get("capability") or "chat")
        if not payload.get("language"):
            from deeptutor.services.settings.interface_settings import (
                get_response_language,
            )

            payload = {**payload, "language": get_response_language(default="en")}
        raw_config = dict(payload.get("config", {}) or {})
        runtime_only_keys = (
            "linked_task_ids",
            "_resource_reuse",
            "_persistent_knowledge_bases",
            "_persist_user_message",
            "_regenerate",
            "_regenerated_from_message_id",
            "_superseded_turn_id",
            "followup_question_context",
            "subagent_consult_budget",
            # Turn-level partner routing keys: public on the wire, not part of
            # any capability's config schema.
            "consult_partner_id",
            "partner_discussion_group_id",
        )
        runtime_only_config = {
            key: raw_config.pop(key) for key in runtime_only_keys if key in raw_config
        }
        from deeptutor.runtime.request_contracts import validate_capability_config

        try:
            validated_public_config = validate_capability_config(capability, raw_config)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        payload = {
            **payload,
            "capability": capability,
            # runtime_only_config is re-merged after the strict wire contract
            # validates below; keep it out of the public config here.
            "config": validated_public_config,
        }
        # Strip runtime-only config keys before the strict public contract
        # validates; they are re-merged into the payload afterwards.
        import copy as _copy

        wire = dict(payload)
        raw_cfg = dict(wire.get("config", {}) or {})
        stripped = {
            key: raw_cfg.pop(key)
            for key in (
                "linked_task_ids",
                "_resource_reuse",
                "_persistent_knowledge_bases",
                "_persist_user_message",
                "_regenerate",
                "_regenerated_from_message_id",
                "_superseded_turn_id",
                "followup_question_context",
                "subagent_consult_budget",
            )
            if key in raw_cfg
        }
        wire["config"] = raw_cfg
        from deeptutor.services.session.turns.request_preparer import TurnRequest

        payload = TurnRequest.model_validate(
            {key: value for key, value in wire.items() if key != "type"}
        ).to_payload()
        payload = {**payload, "config": {**(payload.get("config") or {}), **stripped, **runtime_only_config}}
        try:
            access = require_capability(capability)
            if access is not None:
                raw_config = payload.get("config")
                render_mode = (
                    str(raw_config.get("render_mode") or "auto")
                    if isinstance(raw_config, dict)
                    else "auto"
                )
                if (
                    capability in {"visualize", "math_animator"}
                    and render_mode in {"manim_video", "manim_image"}
                    and access.resolved.values.get("feature.manim") is not True
                ):
                    raise CommercialAccessDenied(
                        "feature_not_in_plan",
                        "Manim rendering is not included in the current plan.",
                    )
            lease = await acquire_commercial_turn_lease(capability=capability)
        except CommercialAccessDenied as exc:
            # The transport layer already treats RuntimeError as a rejected
            # turn. Preserve a stable machine-readable reason instead of
            # leaking provider or billing internals into the response.
            raise RuntimeError(exc.code) from exc

        try:
            return await self._start_turn_impl(
                payload,
                replace_assistant_message_id=replace_assistant_message_id,
                commercial_lease=lease,
            )
        except BaseException:
            await lease.release()
            raise

    async def _start_turn_impl(
        self,
        payload: dict[str, Any],
        *,
        replace_assistant_message_id: int | str | None = None,
        commercial_lease: Any = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if commercial_lease is not None:
            try:
                await commercial_lease.settle_running()
            except Exception:
                pass
        result = await TurnRequestPreparer.start_turn(
            self,
            payload,
            replace_assistant_message_id=replace_assistant_message_id,
        )
        if commercial_lease is not None:
            try:
                await commercial_lease.release()
            except Exception:
                pass
        return result


import threading

_runtime_lock = threading.Lock()
_runtime_instances: dict[str, TurnRuntimeManager] = {}


def get_turn_runtime_manager() -> TurnRuntimeManager:
    from deeptutor.services.session import get_session_store
    from deeptutor.services.session.scope import store_scope

    store = get_session_store()
    key = store_scope(store).cache_key
    with _runtime_lock:
        if key not in _runtime_instances:
            _runtime_instances[key] = TurnRuntimeManager(store=store)
        return _runtime_instances[key]


__all__ = ["TurnRuntimeManager", "cancel_turns_for_user", "get_turn_runtime_manager"]

async def cancel_turns_for_user(user_id: str) -> int:
    """Quiesce local in-flight turns before deleting an owner's workspace."""
    with _runtime_lock:
        managers = tuple(
            {
                id(manager): manager
                for manager in _runtime_instances.values()
                if getattr(manager, "owner_id", "") == user_id
            }.values()
        )
    if not managers:
        return 0
    cancelled = await asyncio.gather(*(manager.cancel_all_turns() for manager in managers))
    return sum(cancelled)


__all__ = ["TurnRuntimeManager", "cancel_turns_for_user", "get_turn_runtime_manager"]