"""Cross-user authorization regression tests for shared API resources."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_tokens(monkeypatch):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services.auth import TokenPayload

    tokens = {
        "alice-token": TokenPayload(username="alice", role="user", user_id="u_alice"),
        "bob-token": TokenPayload(username="bob", role="user", user_id="u_bob"),
        "admin-token": TokenPayload(username="root", role="admin", user_id="u_root"),
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    return auth_router


def _app_with(router, prefix: str, auth_router) -> FastAPI:
    app = FastAPI()
    app.include_router(
        router,
        prefix=prefix,
        dependencies=[Depends(auth_router.require_auth)],
    )
    return app


def _write_public_output(as_user, uid: str, content: bytes, *, role: str = "user") -> str:
    from deeptutor.multi_user.paths import get_current_path_service

    with as_user(uid, role=role):
        service = get_current_path_service()
        relative = Path("workspace/chat/chat/session-1/exec/report.pdf")
        target = service.get_public_outputs_root() / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        assert service.is_public_output_path(target)
    return relative.as_posix()


def test_outputs_download_requires_auth_and_resolves_current_owner(
    mu_isolated_root, as_user, auth_tokens
) -> None:
    from deeptutor.api.routers import outputs

    relative = _write_public_output(as_user, "u_alice", b"alice-output")
    _write_public_output(as_user, "u_bob", b"bob-output")

    app = _app_with(outputs.router, "/api/outputs", auth_tokens)
    with TestClient(app) as client:
        assert client.get(f"/api/outputs/{relative}").status_code == 401

        alice = client.get(f"/api/outputs/{relative}", headers=_auth("alice-token"))
        bob = client.get(f"/api/outputs/{relative}", headers=_auth("bob-token"))

    assert alice.status_code == 200
    assert alice.content == b"alice-output"
    assert bob.status_code == 200
    assert bob.content == b"bob-output"


def test_outputs_download_does_not_fall_back_to_admin_workspace(
    mu_isolated_root, as_user, auth_tokens
) -> None:
    from deeptutor.api.routers import outputs

    relative = _write_public_output(
        as_user,
        "local-admin",
        b"admin-output",
        role="admin",
    )
    app = _app_with(outputs.router, "/api/outputs", auth_tokens)

    with TestClient(app) as client:
        response = client.get(f"/api/outputs/{relative}", headers=_auth("alice-token"))

    assert response.status_code == 404


def test_outputs_rejects_traversal_private_paths_and_symlink_escape(
    mu_isolated_root, as_user, auth_tokens
) -> None:
    from deeptutor.api.routers import outputs
    from deeptutor.multi_user.paths import get_current_path_service

    with as_user("u_alice"):
        service = get_current_path_service()
        root = service.get_public_outputs_root()
        private = service.get_settings_dir() / "private.bin"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_bytes(b"private")

        outside = root.parent / "outside.pdf"
        outside.write_bytes(b"outside")
        link = root / "workspace/chat/chat/session-1/exec/link.pdf"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(outside)
        except OSError as exc:  # pragma: no cover - uncommon restricted FS
            pytest.skip(f"symlinks unavailable: {exc}")

    app = _app_with(outputs.router, "/api/outputs", auth_tokens)
    with TestClient(app) as client:
        headers = _auth("alice-token")
        private_response = client.get("/api/outputs/settings/private.bin", headers=headers)
        traversal_response = client.get(
            "/api/outputs/%2e%2e/outside.pdf",
            headers=headers,
        )
        symlink_response = client.get(
            "/api/outputs/workspace/chat/chat/session-1/exec/link.pdf",
            headers=headers,
        )

    assert private_response.status_code == 404
    assert traversal_response.status_code == 404
    assert symlink_response.status_code == 404


def test_active_output_is_download_only_with_restrictive_headers(
    mu_isolated_root, as_user, auth_tokens
) -> None:
    from deeptutor.api.routers import outputs
    from deeptutor.multi_user.paths import get_current_path_service

    relative = Path("workspace/chat/chat/session-1/exec/report.html")
    with as_user("u_alice"):
        service = get_current_path_service()
        target = service.get_public_outputs_root() / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<script>window.top.pwned = true</script>", encoding="utf-8")
        assert service.is_public_output_path(target)

    app = _app_with(outputs.router, "/api/outputs", auth_tokens)
    with TestClient(app) as client:
        response = client.get(
            f"/api/outputs/{relative.as_posix()}",
            headers=_auth("alice-token"),
        )

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"


def test_artifact_discovery_does_not_fall_back_when_user_scope_resolution_fails(
    mu_isolated_root, as_user, monkeypatch
) -> None:
    from deeptutor.multi_user import paths as multi_user_paths
    from deeptutor.services.sandbox import artifacts

    workdir = mu_isolated_root / "work"
    workdir.mkdir()
    (workdir / "report.pdf").write_bytes(b"not-public")

    def fail_closed():
        raise RuntimeError("scope resolution failed")

    monkeypatch.setattr(multi_user_paths, "get_current_path_service", fail_closed)
    with as_user("u_alice"):
        with pytest.raises(RuntimeError, match="scope resolution failed"):
            artifacts.collect_public_artifacts(workdir)


def test_custom_attachment_root_is_namespaced_by_owner(
    mu_isolated_root, as_user, monkeypatch
) -> None:
    from deeptutor.services.storage import attachment_store as store_module

    shared_root = mu_isolated_root / "configured-attachments"
    monkeypatch.setattr(
        store_module,
        "load_system_settings",
        lambda: {"chat_attachment_dir": str(shared_root)},
    )
    store_module.reset_attachment_store()

    with as_user("u_alice"):
        alice_root = store_module.get_attachment_store().root
    with as_user("u_bob"):
        bob_root = store_module.get_attachment_store().root
    with as_user("local-admin", role="admin"):
        admin_root = store_module.get_attachment_store().root

    assert alice_root != bob_root
    assert alice_root.is_relative_to(shared_root)
    assert bob_root.is_relative_to(shared_root)
    # Preserve the historical configured directory for the local/admin owner.
    assert admin_root == shared_root.resolve()


def test_attachment_download_uses_owner_scoped_store_even_with_same_session_id(
    mu_isolated_root, as_user, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import attachments
    from deeptutor.services.storage import attachment_store as store_module

    shared_root = mu_isolated_root / "configured-attachments"
    monkeypatch.setattr(
        store_module,
        "load_system_settings",
        lambda: {"chat_attachment_dir": str(shared_root)},
    )
    store_module.reset_attachment_store()

    async def seed() -> None:
        with as_user("u_alice"):
            await store_module.get_attachment_store().put(
                session_id="shared-session",
                attachment_id="same-id",
                filename="notes.txt",
                data=b"alice-attachment",
            )
        with as_user("u_bob"):
            await store_module.get_attachment_store().put(
                session_id="shared-session",
                attachment_id="same-id",
                filename="notes.txt",
                data=b"bob-attachment",
            )

    asyncio.run(seed())
    app = _app_with(attachments.router, "/api/attachments", auth_tokens)
    url = "/api/attachments/shared-session/same-id/notes.txt"
    with TestClient(app) as client:
        alice = client.get(url, headers=_auth("alice-token"))
        bob = client.get(url, headers=_auth("bob-token"))

    assert alice.status_code == 200
    assert alice.content == b"alice-attachment"
    assert bob.status_code == 200
    assert bob.content == b"bob-attachment"


def test_active_attachment_is_download_only_with_restrictive_headers(
    mu_isolated_root, as_user, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import attachments
    from deeptutor.services.storage import attachment_store as store_module

    shared_root = mu_isolated_root / "configured-attachments"
    monkeypatch.setattr(
        store_module,
        "load_system_settings",
        lambda: {"chat_attachment_dir": str(shared_root)},
    )
    store_module.reset_attachment_store()

    async def seed() -> None:
        with as_user("u_alice"):
            await store_module.get_attachment_store().put(
                session_id="session-html",
                attachment_id="active-id",
                filename="payload.svg",
                data=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            )

    asyncio.run(seed())
    app = _app_with(attachments.router, "/api/attachments", auth_tokens)
    with TestClient(app) as client:
        response = client.get(
            "/api/attachments/session-html/active-id/payload.svg",
            headers=_auth("alice-token"),
        )

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"


def test_global_capability_and_memory_settings_require_admin(
    mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import capabilities_settings, memory
    from deeptutor.services.config import capabilities_settings as capability_service
    from deeptutor.services.memory import settings as memory_service

    writes: list[str] = []
    monkeypatch.setattr(
        capability_service,
        "save_capabilities_settings",
        lambda payload: writes.append("capabilities") or payload,
    )
    monkeypatch.setattr(
        capability_service,
        "capabilities_settings_dict",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        memory_service,
        "save_memory_settings",
        lambda payload: writes.append("memory") or payload,
    )
    monkeypatch.setattr(memory_service, "memory_settings_dict", lambda: {"ok": True})

    app = FastAPI()
    app.include_router(
        capabilities_settings.router,
        prefix="/api/v1/capabilities",
        dependencies=[Depends(auth_tokens.require_auth)],
    )
    app.include_router(
        memory.router,
        prefix="/api/v1/memory",
        dependencies=[Depends(auth_tokens.require_auth)],
    )

    with TestClient(app) as client:
        assert (
            client.get(
                "/api/v1/capabilities/settings",
                headers=_auth("alice-token"),
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/v1/memory/settings",
                headers=_auth("alice-token"),
            ).status_code
            == 403
        )
        capability_denied = client.put(
            "/api/v1/capabilities/settings",
            headers=_auth("alice-token"),
            json={"chat": {"temperature": 0.1}},
        )
        memory_denied = client.put(
            "/api/v1/memory/settings",
            headers=_auth("alice-token"),
            json={"update": {"l2_budget": 1}},
        )
        capability_ok = client.put(
            "/api/v1/capabilities/settings",
            headers=_auth("admin-token"),
            json={"chat": {"temperature": 0.1}},
        )
        memory_ok = client.put(
            "/api/v1/memory/settings",
            headers=_auth("admin-token"),
            json={"update": {"l2_budget": 1}},
        )
        assert (
            client.get(
                "/api/v1/capabilities/settings",
                headers=_auth("admin-token"),
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/v1/memory/settings",
                headers=_auth("admin-token"),
            ).status_code
            == 200
        )

    assert capability_denied.status_code == 403
    assert memory_denied.status_code == 403
    assert capability_ok.status_code == 200
    assert memory_ok.status_code == 200
    assert writes == ["capabilities", "memory"]


class _FakeRuntimeSettings:
    def load_pageindex(self, **_kwargs):
        return {"api_key": "", "api_base_url": "https://example.test"}

    def save_pageindex(self, payload):
        return payload

    def load_llamaindex(self, **_kwargs):
        return {}

    def save_llamaindex(self, payload):
        return payload

    def load_graphrag(self):
        return {}

    def save_graphrag(self, payload):
        return payload

    def load_lightrag(self):
        return {}

    def save_lightrag(self, payload):
        return payload


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/rag-pipelines/pageindex/config", {"api_base_url": "https://example.test"}),
        ("/rag-pipelines/llamaindex/config", {"top_k": 3}),
        ("/rag-pipelines/graphrag/config", {"community_level": 2}),
        ("/rag-pipelines/lightrag/config", {"top_k": 3}),
    ],
)
def test_global_rag_pipeline_settings_require_admin(
    path, payload, mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import knowledge
    from deeptutor.services import config as config_module

    fake = _FakeRuntimeSettings()
    monkeypatch.setattr(config_module, "get_runtime_settings_service", lambda: fake)
    app = _app_with(knowledge.router, "/api/v1/knowledge", auth_tokens)

    with TestClient(app) as client:
        read_denied = client.get(
            f"/api/v1/knowledge{path}",
            headers=_auth("alice-token"),
        )
        denied = client.put(
            f"/api/v1/knowledge{path}",
            headers=_auth("alice-token"),
            json=payload,
        )

    assert read_denied.status_code == 403
    assert denied.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/knowledge/rag-pipelines/model-options",
        "/api/v1/knowledge/rag-pipelines/llamaindex/preflight",
    ],
)
def test_global_rag_catalog_and_preflight_require_admin(
    path, mu_isolated_root, auth_tokens
) -> None:
    from deeptutor.api.routers import knowledge

    app = _app_with(knowledge.router, "/api/v1/knowledge", auth_tokens)
    with TestClient(app) as client:
        response = client.get(path, headers=_auth("alice-token"))
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/connect-obsidian", {"name": "vault", "vault_path": "/tmp/vault"}),
        (
            "/probe-folder",
            {"folder_path": "/tmp/index", "rag_provider": "llamaindex"},
        ),
        (
            "/connect-folder",
            {
                "name": "linked",
                "folder_path": "/tmp/index",
                "rag_provider": "llamaindex",
            },
        ),
        (
            "/probe-lightrag-server",
            {"server_url": "http://127.0.0.1:9621", "api_key": "secret"},
        ),
        (
            "/connect-lightrag-server",
            {
                "name": "remote",
                "server_url": "http://127.0.0.1:9621",
                "api_key": "secret",
            },
        ),
        (
            "/probe-ima",
            {"client_id": "client", "api_key": "secret", "knowledge_base_id": "kb"},
        ),
        (
            "/connect-ima",
            {
                "name": "ima",
                "client_id": "client",
                "api_key": "secret",
                "knowledge_base_id": "kb",
            },
        ),
        ("/owned/link-folder", {"folder_path": "/tmp/documents"}),
    ],
)
def test_host_and_remote_kb_connections_require_admin(
    path, payload, mu_isolated_root, auth_tokens
) -> None:
    from deeptutor.api.routers import knowledge

    app = _app_with(knowledge.router, "/api/v1/knowledge", auth_tokens)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/knowledge{path}",
            headers=_auth("alice-token"),
            json=payload,
        )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/owned/linked-folders"),
        ("delete", "/owned/linked-folders/folder-1"),
        ("post", "/owned/sync-folder/folder-1"),
    ],
)
def test_linked_folder_management_requires_admin(
    method, path, mu_isolated_root, auth_tokens
) -> None:
    from deeptutor.api.routers import knowledge

    app = _app_with(knowledge.router, "/api/v1/knowledge", auth_tokens)
    with TestClient(app) as client:
        response = client.request(
            method,
            f"/api/v1/knowledge{path}",
            headers=_auth("alice-token"),
        )

    assert response.status_code == 403


def test_ordinary_kb_upload_route_is_not_admin_gated(mu_isolated_root, auth_tokens) -> None:
    """A regular user's own uploaded KB remains a supported Phase-1 path."""
    from deeptutor.api.routers import knowledge

    app = _app_with(knowledge.router, "/api/v1/knowledge", auth_tokens)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/create",
            headers=_auth("alice-token"),
        )

    # Missing multipart fields are invalid, but authorization must not reject
    # the ordinary upload route as an admin-only operation.
    assert response.status_code == 422


class _SubagentManager:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}

    def get_metadata(self, name: str) -> dict:
        return dict(self.entries.get(name, {}))

    def register_subagent_connection(
        self, name: str, agent_kind: str, *, cwd: str = "", partner_id: str = ""
    ) -> dict:
        entry = {
            "name": name,
            "type": "subagent",
            "agent_kind": agent_kind,
            "cwd": cwd,
            "partner_id": partner_id,
        }
        self.entries[name] = entry
        return entry


def test_subagent_host_discovery_and_global_settings_require_admin(
    mu_isolated_root, auth_tokens
) -> None:
    from deeptutor.api.routers import subagents

    app = _app_with(subagents.router, "/api/v1/subagents", auth_tokens)
    with TestClient(app) as client:
        responses = [
            client.get("/api/v1/subagents/detect", headers=_auth("alice-token")),
            client.get("/api/v1/subagents/backends/options", headers=_auth("alice-token")),
            client.post(
                "/api/v1/subagents/backends/claude_code/sync",
                headers=_auth("alice-token"),
            ),
            client.get("/api/v1/subagents/settings", headers=_auth("alice-token")),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]


def test_subagent_local_cli_connection_and_message_require_admin(
    mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import subagents
    from deeptutor.services.subagent.types import ConsultResult

    manager = _SubagentManager()
    manager.entries["HostCodex"] = {
        "name": "HostCodex",
        "type": "subagent",
        "agent_kind": "codex",
        "cwd": "/tmp/project",
        "partner_id": "",
    }
    monkeypatch.setattr(subagents, "current_kb_manager", lambda: manager)
    monkeypatch.setattr(subagents, "list_backend_kinds", lambda: ["codex", "partner"])
    monkeypatch.setattr(subagents, "assert_path_allowed", lambda path: Path(path))

    class _HostBackend:
        kind = "codex"
        local_cli = True

        async def consult(self, *_args, **_kwargs):
            return ConsultResult(final_text="host result", success=True)

    monkeypatch.setattr(
        "deeptutor.services.subagent.get_backend",
        lambda _kind: _HostBackend(),
    )
    app = _app_with(subagents.router, "/api/v1/subagents", auth_tokens)

    with TestClient(app) as client:
        connect_response = client.post(
            "/api/v1/subagents/connections",
            headers=_auth("alice-token"),
            json={"name": "Another", "agent_kind": "codex", "cwd": "/tmp/project"},
        )
        message_response = client.post(
            "/api/v1/subagents/connections/HostCodex/message",
            headers=_auth("alice-token"),
            json={"chat_session_id": "chat-1", "message": "inspect the host"},
        )

    assert connect_response.status_code == 403
    assert message_response.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/partners/assigned/chat",
        "/api/v1/partners/assigned/chat/execute-stream",
    ],
)
def test_partner_http_chat_requires_model_access(
    path, mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import partners

    monkeypatch.setattr(partners, "assert_partner_allowed", lambda _partner_id: None, raising=False)
    monkeypatch.setattr(
        partners,
        "has_capability_access",
        lambda _capability: False,
        raising=False,
    )

    class _StubManager:
        def partner_exists(self, _partner_id: str) -> bool:
            return True

    monkeypatch.setattr(partners, "get_partner_manager", lambda: _StubManager())
    monkeypatch.setattr(partners, "can_use_partner", lambda _pid, _user=None: True)

    async def must_not_start(_partner_id: str):
        raise AssertionError("partner execution must not start")

    monkeypatch.setattr(partners, "_ensure_running_partner", must_not_start)
    app = _app_with(partners.router, "/api/v1/partners", auth_tokens)
    with TestClient(app) as client:
        response = client.post(
            path,
            headers=_auth("alice-token"),
            json={"content": "consume platform model"},
        )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/partners/unassigned/chat",
        "/api/v1/partners/unassigned/chat/execute-stream",
    ],
)
def test_partner_http_chat_requires_partner_assignment(
    path, mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from fastapi import HTTPException

    from deeptutor.api.routers import partners

    def deny_unassigned(_partner_id: str) -> None:
        raise HTTPException(status_code=403, detail="Partner is not assigned to you")

    monkeypatch.setattr(partners, "assert_partner_allowed", deny_unassigned)
    monkeypatch.setattr(partners, "has_capability_access", lambda _capability: True)

    class _StubManager:
        def partner_exists(self, _partner_id: str) -> bool:
            return True

    monkeypatch.setattr(partners, "get_partner_manager", lambda: _StubManager())
    monkeypatch.setattr(partners, "can_use_partner", lambda _pid, _user=None: True)

    async def must_not_start(_partner_id: str):
        raise AssertionError("unassigned partner execution must not start")

    monkeypatch.setattr(partners, "_ensure_running_partner", must_not_start)
    app = _app_with(partners.router, "/api/v1/partners", auth_tokens)
    with TestClient(app) as client:
        response = client.post(
            path,
            headers=_auth("alice-token"),
            json={"content": "use unassigned partner"},
        )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("path", "request_kwargs"),
    [
        ("/api/v1/voice/tts", {"json": {"text": "hello"}}),
        (
            "/api/v1/voice/stt",
            {"files": {"file": ("clip.webm", b"audio", "audio/webm")}},
        ),
    ],
)
def test_voice_platform_credentials_require_service_grant(
    path, request_kwargs, mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import voice

    async def must_not_call(*_args, **_kwargs):
        raise AssertionError("voice provider must not be called")

    monkeypatch.setattr(voice, "synthesize_speech", must_not_call)
    monkeypatch.setattr(voice, "transcribe_audio", must_not_call)
    app = _app_with(voice.router, "/api/v1/voice", auth_tokens)
    with TestClient(app) as client:
        response = client.post(
            path,
            headers=_auth("alice-token"),
            **request_kwargs,
        )

    assert response.status_code == 403


def test_system_connection_tests_require_admin_but_status_remains_user_visible(
    mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import system

    class _LLMConfig:
        model = "test-model"
        base_url = "https://example.test/v1"
        api_key = "test"
        binding = "openai"

    monkeypatch.setattr(system, "get_llm_config", lambda: _LLMConfig())

    async def fake_complete(**_kwargs):
        return "OK"

    monkeypatch.setattr(system, "llm_complete", fake_complete)
    app = _app_with(system.router, "/api/v1/system", auth_tokens)

    with TestClient(app) as client:
        status_response = client.get("/api/v1/system/status", headers=_auth("alice-token"))
        test_response = client.post("/api/v1/system/test/llm", headers=_auth("alice-token"))

    assert status_response.status_code == 200
    assert test_response.status_code == 403


def test_memory_runs_are_hidden_and_immutable_across_owners(
    mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import memory
    from deeptutor.services.memory.consolidator import runs

    runs.reset_run_manager_for_tests()

    async def blocking_runner(_on_event):
        await asyncio.Event().wait()

    monkeypatch.setattr(memory, "_runner_for", lambda _req: blocking_runner)
    app = _app_with(memory.router, "/api/v1/memory", auth_tokens)

    with TestClient(app) as client:
        started = client.post(
            "/api/v1/memory/runs/start",
            headers=_auth("alice-token"),
            json={"layer": "L2", "key": "chat", "mode": "update"},
        )
        assert started.status_code == 200
        run_id = started.json()["id"]

        assert (
            client.get(f"/api/v1/memory/runs/{run_id}", headers=_auth("bob-token")).status_code
            == 404
        )
        listed = client.get("/api/v1/memory/runs", headers=_auth("bob-token"))
        assert listed.status_code == 200
        assert listed.json() == {"runs": []}
        assert (
            client.post(
                f"/api/v1/memory/runs/{run_id}/cancel",
                headers=_auth("bob-token"),
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/memory/runs/{run_id}/undo",
                headers=_auth("bob-token"),
            ).status_code
            == 404
        )

        owner = client.get(f"/api/v1/memory/runs/{run_id}", headers=_auth("alice-token"))
        assert owner.status_code == 200
        assert (
            client.post(
                f"/api/v1/memory/runs/{run_id}/cancel",
                headers=_auth("alice-token"),
            ).status_code
            == 200
        )

    runs.reset_run_manager_for_tests()


def test_memory_run_event_replay_is_hidden_across_owners(
    mu_isolated_root, auth_tokens, monkeypatch
) -> None:
    from deeptutor.api.routers import memory
    from deeptutor.services.memory.consolidator import runs

    runs.reset_run_manager_for_tests()

    async def finished_runner(on_event):
        await on_event({"stage": "progress"})

    monkeypatch.setattr(memory, "_runner_for", lambda _req: finished_runner)
    app = _app_with(memory.router, "/api/v1/memory", auth_tokens)

    with TestClient(app) as client:
        started = client.post(
            "/api/v1/memory/runs/start",
            headers=_auth("alice-token"),
            json={"layer": "L2", "key": "chat", "mode": "update"},
        )
        run_id = started.json()["id"]
        for _ in range(10):
            owner = client.get(
                f"/api/v1/memory/runs/{run_id}",
                headers=_auth("alice-token"),
            )
            if owner.json()["status"] in {"done", "cancelled", "error"}:
                break
        assert owner.json()["status"] == "done"

        denied = client.get(
            f"/api/v1/memory/runs/{run_id}/events",
            headers=_auth("bob-token"),
        )

    assert denied.status_code == 404
    runs.reset_run_manager_for_tests()
