from __future__ import annotations

from datetime import timedelta
import json

import pytest

from deeptutor.commercial import ImmutablePlanVersion, InMemoryCommercialRepository
from deeptutor.commercial.postgres import PostgresCommercialRepository
from deeptutor.commercial.runtime import (
    CommercialRuntime,
    CommercialSettings,
    get_commercial_runtime,
    reset_commercial_runtime_for_tests,
)
from deeptutor.commercial.seed import TrialV1ModelBinding, build_trial_v1_plan, seed_trial_v1

from .conftest import seed_customer_and_plan


def test_trial_v1_seed_is_complete_and_binds_an_explicit_model_catalog_snapshot() -> None:
    seed = build_trial_v1_plan(
        TrialV1ModelBinding(
            profile_id="trial-default-2026-08",
            model_ids=("openai/gpt-5-mini", "openai/text-embedding-3-small"),
        )
    )

    expected_true = {
        "capability.chat",
        "capability.mastery_path",
        "capability.deep_solve",
        "capability.deep_research",
        "capability.deep_question",
        "capability.visualize",
        "feature.byok",
    }
    expected_false = {
        "tool.sandbox",
        "tool.exec",
        "tool.cron",
        "feature.partners",
        "feature.mcp",
        "feature.manim",
    }
    assert all(seed.entitlements[key] is True for key in expected_true)
    assert all(seed.entitlements[key] is False for key in expected_false)
    assert seed.entitlements["limits.kb_count"] == 2
    assert seed.entitlements["limits.storage_bytes"] == 524_288_000
    assert seed.entitlements["limits.upload_bytes"] == 26_214_400
    assert seed.entitlements["limits.concurrent_turns"] == 1
    assert seed.entitlements["quota.concurrent_turns"] == {
        "limit": 1,
        "window": "subscription",
    }
    assert seed.entitlements["budget.platform_cost_credits"] == 2_000
    assert "quota.platform_cost_credits" not in seed.entitlements
    assert seed.entitlements["quota.llm_tokens"] == {
        "limit": 100_000,
        "window": "utc_day",
    }
    assert seed.entitlements["quota.embedding_tokens"]["limit"] == 500_000
    assert seed.entitlements["quota.mineru_pages"]["limit"] == 20
    assert seed.entitlements["limits.mineru_max_pages_per_file"] == 10
    assert seed.entitlements["models.llm"] == {
        "profile_id": "trial-default-2026-08",
        "model_ids": ["openai/gpt-5-mini", "openai/text-embedding-3-small"],
    }


@pytest.mark.asyncio
async def test_reseeding_trial_v1_with_model_drift_requires_v2(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    await seed_trial_v1(
        service,
        TrialV1ModelBinding("catalog-a", ("model-a",)),
    )
    with pytest.raises(ImmutablePlanVersion):
        await seed_trial_v1(
            service,
            TrialV1ModelBinding("catalog-b", ("model-b",)),
        )


def test_commercial_settings_default_off_and_enabled_without_database_fails_closed() -> None:
    disabled = CommercialSettings.from_env({})
    assert disabled.enabled is False
    assert disabled.database_url is None

    with pytest.raises(ValueError, match="DATABASE_URL"):
        CommercialSettings.from_env({"DEEPTUTOR_COMMERCIAL_ENABLED": "true"})

    with pytest.raises(ValueError, match="TRIAL_LLM_MODELS"):
        CommercialSettings.from_env(
            {
                "DEEPTUTOR_COMMERCIAL_ENABLED": "true",
                "DEEPTUTOR_COMMERCIAL_DATABASE_URL": "postgresql://db/test",
            }
        )

    stale_disabled = CommercialSettings.from_env(
        {
            "DEEPTUTOR_COMMERCIAL_ENABLED": "false",
            "DEEPTUTOR_COMMERCIAL_DATABASE_URL": "not-a-url",
            "DEEPTUTOR_COMMERCIAL_DATABASE_URL_FILE": "/missing/secret",
            "DEEPTUTOR_COMMERCIAL_TRIAL_DAYS": "not-an-int",
            "DEEPTUTOR_COMMERCIAL_TRIAL_LLM_MODELS": "not-json",
        }
    )
    assert stale_disabled == CommercialSettings(enabled=False)


def test_database_url_file_is_exclusive_and_trial_model_binding_is_explicit(tmp_path) -> None:
    secret = tmp_path / "database-url"
    secret.write_text(" postgresql://secret/db\n", encoding="utf-8")
    models = json.dumps({"profile_id": "trial-models-v1", "model_ids": ["model-a"]})
    settings = CommercialSettings.from_env(
        {
            "DEEPTUTOR_COMMERCIAL_ENABLED": "true",
            "DEEPTUTOR_COMMERCIAL_DATABASE_URL_FILE": str(secret),
            "DEEPTUTOR_COMMERCIAL_TRIAL_DAYS": "7",
            "DEEPTUTOR_COMMERCIAL_TRIAL_LLM_MODELS": models,
        }
    )
    assert settings.database_url == "postgresql://secret/db"
    assert settings.trial_model_binding.profile_id == "trial-models-v1"

    with pytest.raises(ValueError, match="only one"):
        CommercialSettings.from_env(
            {
                "DEEPTUTOR_COMMERCIAL_ENABLED": "true",
                "DEEPTUTOR_COMMERCIAL_DATABASE_URL": "postgresql://plain/db",
                "DEEPTUTOR_COMMERCIAL_DATABASE_URL_FILE": str(secret),
            }
        )

    with pytest.raises(ValueError, match="Trial v1 requires"):
        CommercialSettings.from_env(
            {
                "DEEPTUTOR_COMMERCIAL_ENABLED": "true",
                "DEEPTUTOR_COMMERCIAL_DATABASE_URL": "postgresql://db/test",
                "DEEPTUTOR_COMMERCIAL_TRIAL_DAYS": "14",
                "DEEPTUTOR_COMMERCIAL_TRIAL_LLM_MODELS": models,
            }
        )


@pytest.mark.asyncio
async def test_runtime_disabled_is_a_noop_without_importing_or_connecting_asyncpg() -> None:
    runtime = CommercialRuntime(CommercialSettings(enabled=False))
    assert await runtime.startup() is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_seeds_stable_trial_plan_and_exposes_owner_helpers(monkeypatch) -> None:
    class FakeRepository(InMemoryCommercialRepository):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    repository = FakeRepository()

    async def connect(_cls, _dsn, **_kwargs):
        return repository

    monkeypatch.setattr(PostgresCommercialRepository, "connect", classmethod(connect))
    runtime = CommercialRuntime(
        CommercialSettings(
            enabled=True,
            database_url="postgresql://unused/test",
            trial_model_binding=TrialV1ModelBinding("catalog-v1", ("model-a",)),
        )
    )
    await runtime.startup()

    assert runtime.trial_plan_version_id is not None
    trial = await runtime.ensure_trial_for_owner("runtime-owner")
    retry = await runtime.ensure_trial_for_owner("runtime-owner")
    assert retry == trial
    assert (await runtime.resolve_for_owner("runtime-owner")).active

    await runtime.shutdown()
    assert repository.closed is True


@pytest.mark.asyncio
async def test_process_runtime_singleton_is_shared_and_resettable() -> None:
    await reset_commercial_runtime_for_tests()
    first = get_commercial_runtime({})
    second = get_commercial_runtime(
        {"DEEPTUTOR_COMMERCIAL_ENABLED": "false", "ignored": "different"}
    )
    assert first is second
    await reset_commercial_runtime_for_tests()
    assert get_commercial_runtime({}) is not first
    await reset_commercial_runtime_for_tests()


@pytest.mark.asyncio
async def test_customer_export_is_sanitized_and_erase_is_idempotent(commercial_harness) -> None:
    service, repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)
    reservation = await service.reserve_usage(customer.id, "llm_tokens", 20, "private-request")
    await service.finalize_usage(
        reservation.id,
        actual_quantity=10,
        provider="openai",
        model="gpt-5-mini",
        usage_units={"tokens": 10},
        price_version="prices-v1",
        cost_micros=50,
        is_estimated=False,
    )
    webhook = await service.record_webhook_event(
        provider="stripe",
        external_event_id="evt-secret",
        event_type="future.event",
        payload={"card": "must-not-export"},
        customer_id=customer.id,
    )

    exported = await service.export_customer_data("user-alice")
    serialized = json.dumps(exported)
    assert exported["customer"]["owner_id"] == "user-alice"
    assert len(exported["subscriptions"]) == 1
    assert len(exported["usage_events"]) == 1
    assert "must-not-export" not in serialized
    assert "evt-secret" not in serialized
    assert "external_customer_id" not in exported["customer"]

    assert await service.erase_customer("user-alice") is True
    assert await service.erase_customer("user-alice") is False
    assert await service.erase_customer("does-not-exist") is False
    assert await repository.get_billing_customer(customer.id) is None
    replay_after_erasure = await service.record_webhook_event(
        provider="stripe",
        external_event_id="evt-secret",
        event_type="future.event",
        payload={"card": "new-owner-neutral-copy"},
    )
    assert replay_after_erasure.id != webhook.id
