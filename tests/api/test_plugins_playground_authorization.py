"""Authorization boundary for the process-wide Plugins Playground."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def plugins_modules(monkeypatch):
    from deeptutor.api.routers import auth, plugins_api
    from deeptutor.services.auth import TokenPayload

    tokens = {
        "user-token": TokenPayload(username="alice", role="user", user_id="u_alice"),
        "admin-token": TokenPayload(username="root", role="admin", user_id="u_root"),
    }
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda token: tokens.get(token))
    return auth, plugins_api


def _app(plugins_api) -> FastAPI:
    app = FastAPI()
    app.include_router(plugins_api.router, prefix="/api/v1/plugins")
    return app


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/v1/plugins/list", None),
        ("post", "/api/v1/plugins/tools/cron/execute", {"params": {}}),
        ("post", "/api/v1/plugins/tools/cron/execute-stream", {"params": {}}),
        (
            "post",
            "/api/v1/plugins/capabilities/chat/execute-stream",
            {"content": "bypass limits", "bot_id": "platform-partner"},
        ),
    ],
)
def test_ordinary_user_cannot_reach_any_playground_surface(
    plugins_modules,
    monkeypatch,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    _auth, plugins_api = plugins_modules

    def registry_accessed():
        pytest.fail("ordinary users must be rejected before global registry access")

    monkeypatch.setattr(plugins_api, "get_tool_registry", registry_accessed)
    monkeypatch.setattr(plugins_api, "get_capability_registry", registry_accessed)
    client = TestClient(_app(plugins_api))

    response = client.request(
        method,
        path,
        headers={"Authorization": "Bearer user-token"},
        json=payload,
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required"}


def test_playground_requires_authentication_when_auth_is_enabled(plugins_modules) -> None:
    _auth, plugins_api = plugins_modules
    response = TestClient(_app(plugins_api)).get("/api/v1/plugins/list")
    assert response.status_code == 401


def test_production_app_mount_keeps_the_playground_admin_boundary(
    plugins_modules,
    monkeypatch,
) -> None:
    _auth, plugins_api = plugins_modules

    def registry_accessed():
        pytest.fail("production mount must reject users before registry access")

    monkeypatch.setattr(plugins_api, "get_tool_registry", registry_accessed)
    monkeypatch.setattr(plugins_api, "get_capability_registry", registry_accessed)
    from deeptutor.api import main as api_main

    response = TestClient(api_main.app).get(
        "/api/v1/plugins/list",
        headers={"Authorization": "Bearer user-token"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required"}


class _EmptyToolRegistry:
    def get_definitions(self) -> list[Any]:
        return []


class _EmptyCapabilityRegistry:
    def get_manifests(self) -> list[Any]:
        return []


@dataclass
class _ToolResult:
    success: bool = True
    content: str = "local result"
    sources: list[Any] | None = None
    metadata: dict[str, Any] | None = None


class _LocalTool:
    async def execute(self, **_params: Any) -> _ToolResult:
        return _ToolResult()


class _LocalToolRegistry(_EmptyToolRegistry):
    def get(self, name: str):
        return _LocalTool() if name == "local_tool" else None


def test_admin_can_list_playground_plugins(plugins_modules, monkeypatch) -> None:
    _auth, plugins_api = plugins_modules
    monkeypatch.setattr(plugins_api, "get_tool_registry", _EmptyToolRegistry)
    monkeypatch.setattr(plugins_api, "get_capability_registry", _EmptyCapabilityRegistry)
    monkeypatch.setattr(plugins_api, "_discover_plugins", lambda: [])

    response = TestClient(_app(plugins_api)).get(
        "/api/v1/plugins/list",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"tools": [], "capabilities": [], "plugins": []}


def test_auth_disabled_keeps_local_tool_execution_compatible(
    plugins_modules,
    monkeypatch,
) -> None:
    auth, plugins_api = plugins_modules
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(plugins_api, "get_tool_registry", _LocalToolRegistry)

    response = TestClient(_app(plugins_api)).post(
        "/api/v1/plugins/tools/local_tool/execute",
        json={"params": {"value": 1}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "content": "local result",
        "sources": None,
        "metadata": None,
    }
