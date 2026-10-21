from types import SimpleNamespace

import pytest

from deeptutor.api.main import app, health_live, health_ready
from tests.api.route_introspection import iter_effective_route_paths


def test_only_canonical_transport_and_resource_routes_are_registered() -> None:
    paths = set(iter_effective_route_paths(app))

    required = {
        "/api/books",
        "/api/documents",
        "/api/knowledge-bases",
        "/api/mastery-paths/topics",
        "/api/notebooks",
        "/api/personas",
        "/api/sessions",
        "/api/system/runtime",
        "/files/attachments/{session_id}/{attachment_id}/{filename:path}",
        "/files/outputs/{output_path:path}",
        "/ws",
        "/ws/books",
    }
    assert required <= paths

    retired_prefixes = (
        "/api/v1",
        "/api/attachments",
        "/api/book",
        "/api/chat",
        "/api/co_writer",
        "/api/knowledge",
        "/api/learning",
        "/api/notebook",
        "/api/outputs",
    )
    # Fork surface: BYOK, the plugins playground, and the commercial account
    # surface predate the v1 retirement and still live under /api/v1 until
    # they are rehomed (see the matching exemptions in
    # web/tests/no-v1-chat-surface.test.ts).
    fork_prefixes = (
        "/api/v1/byok",
        "/api/v1/plugins",
        "/api/v1/admin",
        "/api/v1/commercial",
    )
    assert not {
        path
        for path in paths
        if any(path == prefix or path.startswith(prefix + "/") for prefix in retired_prefixes)
        and not any(path == fp or path.startswith(fp + "/") for fp in fork_prefixes)
    }
    assert "/api/system/runtime-topology" not in paths


class _Coordinator:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def health(self) -> bool:
        return self.healthy


@pytest.mark.asyncio
async def test_health_endpoints_distinguish_liveness_and_readiness() -> None:
    assert await health_live() == {"status": "alive"}
    state = SimpleNamespace(ready=False)
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    not_started = await health_ready(request)
    assert not_started.status_code == 503

    state.ready = True
    state.application_container = SimpleNamespace(coordinator=_Coordinator(False))
    unavailable = await health_ready(request)
    assert unavailable.status_code == 503

    state.application_container.coordinator = _Coordinator(True)
    assert await health_ready(request) == {"status": "ready"}
