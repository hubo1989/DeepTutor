"""Regression tests for the commercial wiring in ``deeptutor.api.main``.

PR20's re-integration rebuilt the API lifespan and dropped two pieces of the
fork's SaaS foundation:

1. the ``startup_commercial_foundation`` / ``shutdown_commercial_foundation``
   hooks that fail-close hosted mode before any request is accepted, and
2. the ``deeptutor.commercial.api.router`` mount at ``/api/v1`` that serves
   ``GET /api/v1/commercial/me``.

These tests pin both back in. The lifespan tests drive the real ``lifespan``
coroutine of the real app with only its external seams (PostgreSQL-bound
commercial startup, coordinator container, background leader, network pings,
filesystem migrations) stubbed, so the ordering and unwind semantics are the
ones production runs. The HTTP test issues a real request against the real
app object to prove the route is mounted and reachable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import deeptutor.api.main as api_main

# ---------------------------------------------------------------------------
# Seam stubs
# ---------------------------------------------------------------------------


class _SentinelRuntime:
    """Returned by the stubbed startup and asserted on the shutdown call."""

    settings = SimpleNamespace(enabled=False)


@pytest.fixture
def commercial_calls(monkeypatch):
    """Record startup/shutdown invocations of the commercial foundation."""
    from deeptutor.commercial import integration as commercial_integration

    calls: dict[str, list] = {"startup": [], "shutdown": []}
    sentinel = _SentinelRuntime()

    async def fake_startup():
        calls["startup"].append(sentinel)
        return sentinel

    async def fake_shutdown(runtime):
        calls["shutdown"].append(runtime)

    monkeypatch.setattr(commercial_integration, "startup_commercial_foundation", fake_startup)
    monkeypatch.setattr(commercial_integration, "shutdown_commercial_foundation", fake_shutdown)
    return calls


def _stub_container(monkeypatch) -> SimpleNamespace:
    """Replace the durable-turn container with an inert in-memory stub."""
    container = SimpleNamespace(
        worker_id="test-worker",
        settings=SimpleNamespace(
            renew_interval_seconds=30.0,
            recovery_interval_seconds=30.0,
        ),
        coordinator=SimpleNamespace(),
        started=False,
        closed=False,
    )

    async def start() -> None:
        container.started = True

    async def run_startup_data_migrations() -> dict[str, list]:
        return {"legacy_chat": [], "workspace_preferences": []}

    async def close() -> None:
        container.closed = True

    async def recover_once() -> None:
        return None

    container.start = start
    container.run_startup_data_migrations = run_startup_data_migrations
    container.close = close
    container.recover_once = recover_once

    import deeptutor.app.container as container_module

    monkeypatch.setattr(container_module, "get_application_container", lambda: container)
    return container


def _stub_supervisor(monkeypatch, *, fail_start: bool = False) -> list[str]:
    """Replace the leader supervisor so no partner/cron/sync service starts."""
    events: list[str] = []

    class _StubSupervisor:
        def __init__(self, *args, **kwargs) -> None:
            events.append("init")

        async def start(self) -> None:
            if fail_start:
                raise RuntimeError("supervisor start failed")
            events.append("start")

        async def close(self) -> None:
            events.append("close")

    import deeptutor.runtime.background_leader as background_leader

    monkeypatch.setattr(background_leader, "BackgroundLeaderSupervisor", _StubSupervisor)
    return events


@pytest.fixture
def quiet_external_seams(monkeypatch):
    """Stub network and filesystem seams the lifespan touches at startup."""
    import deeptutor.events.event_bus as event_bus_module
    import deeptutor.learning.assessment as assessment_module
    import deeptutor.services.cron as cron_module
    import deeptutor.services.llm as llm_module
    import deeptutor.services.memory as memory_module
    import deeptutor.services.path_service as path_service_module
    import deeptutor.services.pocketbase_client as pocketbase_module

    class _StubBus:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    bus = _StubBus()
    monkeypatch.setattr(event_bus_module, "get_event_bus", lambda: bus)

    monkeypatch.setattr(cron_module, "get_cron_service", lambda: SimpleNamespace())

    async def fake_ping() -> None:
        return None

    monkeypatch.setattr(pocketbase_module, "ping_pocketbase", fake_ping)

    monkeypatch.setattr(
        llm_module,
        "get_llm_client",
        lambda: SimpleNamespace(config=SimpleNamespace(model="test-model")),
    )

    async def fake_reconcile() -> tuple[int, int]:
        return 0, 0

    monkeypatch.setattr(assessment_module, "reconcile_linked_assessments", fake_reconcile)

    async def noop() -> None:
        return None

    monkeypatch.setattr(memory_module, "migrate_v1_if_needed", noop)
    monkeypatch.setattr(memory_module, "migrate_partner_surface_if_needed", noop)
    monkeypatch.setattr(
        path_service_module.PathService,
        "migrate_legacy_memory_markdown",
        lambda self: None,
    )
    return bus


@pytest.fixture(autouse=True)
def _isolate_shared_app_state():
    """The app object is a module singleton; give each test a clean state."""
    state = api_main.app.state
    state.application_container = None
    if hasattr(state, "background_supervisor"):
        del state.background_supervisor
    yield


# ---------------------------------------------------------------------------
# Lifespan: commercial foundation hooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_starts_commercial_foundation_before_ready(
    commercial_calls, quiet_external_seams, monkeypatch
) -> None:
    _stub_container(monkeypatch)
    supervisor_events = _stub_supervisor(monkeypatch)

    async with api_main.lifespan(api_main.app):
        # Foundation runs first: by the time the supervisor (the last startup
        # step) has started, commercial startup has already completed.
        assert len(commercial_calls["startup"]) == 1
        assert commercial_calls["shutdown"] == []
        assert supervisor_events == ["init", "start"]
        assert api_main.app.state.ready is True

    assert commercial_calls["shutdown"] == commercial_calls["startup"]
    assert api_main.app.state.ready is False
    assert quiet_external_seams.stopped is True


@pytest.mark.asyncio
async def test_lifespan_failure_after_foundation_unwinds_commercial_runtime(
    commercial_calls, quiet_external_seams, monkeypatch
) -> None:
    _stub_container(monkeypatch)
    _stub_supervisor(monkeypatch, fail_start=True)

    with pytest.raises(RuntimeError, match="supervisor start failed"):
        async with api_main.lifespan(api_main.app):
            pass

    # The crashed lifespan must not leak the commercial PostgreSQL pool.
    assert len(commercial_calls["startup"]) == 1
    assert commercial_calls["shutdown"] == commercial_calls["startup"]
    assert api_main.app.state.ready is False


@pytest.mark.asyncio
async def test_lifespan_commercial_startup_failure_does_not_double_shutdown(
    commercial_calls, quiet_external_seams, monkeypatch
) -> None:
    """A commercial startup crash unwinds itself; the lifespan must not retry."""
    from deeptutor.commercial import integration as commercial_integration

    async def failing_startup():
        commercial_calls["startup"].append("failed")
        raise RuntimeError("commercial bootstrap failed")

    monkeypatch.setattr(commercial_integration, "startup_commercial_foundation", failing_startup)
    _stub_container(monkeypatch)
    _stub_supervisor(monkeypatch)

    with pytest.raises(RuntimeError, match="commercial bootstrap failed"):
        async with api_main.lifespan(api_main.app):
            pass

    assert commercial_calls["shutdown"] == []


# ---------------------------------------------------------------------------
# Router: /api/v1 commercial surface on the real app
# ---------------------------------------------------------------------------


def test_commercial_me_is_served_under_api_v1_on_real_app(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from deeptutor.commercial.runtime import CommercialSettings

    class DisabledRuntime:
        settings = CommercialSettings(enabled=False)

    monkeypatch.setattr(
        "deeptutor.commercial.api.get_commercial_runtime", lambda: DisabledRuntime()
    )
    # Keep the request path deterministic even if the host enables auth.
    monkeypatch.setattr("deeptutor.commercial.access.AUTH_ENABLED", False)

    response = TestClient(api_main.app).get("/api/v1/commercial/me")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "is_admin": False,
        "status": "disabled",
        "valid_until": None,
        "plan_version_id": None,
        "values": {},
        "usage": {},
    }
