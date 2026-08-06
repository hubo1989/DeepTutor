from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException, UploadFile
import pytest

from deeptutor.api.routers import knowledge
from deeptutor.commercial.entitlement_context import (
    CommercialAccess,
    reset_commercial_access,
    set_commercial_access,
)
from deeptutor.commercial.models import ResolvedEntitlements, SubscriptionStatus
from deeptutor.commercial.runtime import CommercialSettings
from deeptutor.commercial.storage_limits import (
    CommercialResourceLimitDenied,
    commercial_owner_resource_lock,
    enforce_kb_count_limit,
    enforce_upload_storage_limits,
)
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope


class _Runtime:
    settings = CommercialSettings(
        enabled=True,
        database_url="postgresql://unused/test",
        trial_model_binding=None,
    )


def _user(root: Path, user_id: str = "u_trial") -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=f"{user_id}@example.com",
        role="user",
        scope=UserScope(kind="user", user_id=user_id, root=root),
    )


def _resolved(
    *,
    root: Path,
    active: bool = True,
    values: dict | None = None,
) -> tuple[CurrentUser, CommercialAccess]:
    now = datetime.now(timezone.utc)
    user = _user(root)
    resolved = ResolvedEntitlements(
        customer_id="cus_trial",
        subscription_id="sub_trial",
        plan_version_id="plan_trial_v1",
        status=SubscriptionStatus.TRIALING if active else SubscriptionStatus.EXPIRED,
        active=active,
        resolved_at=now,
        valid_until=now + timedelta(days=1) if active else now - timedelta(seconds=1),
        values=values
        or {
            "limits.kb_count": 2,
            "limits.upload_bytes": 25,
            "limits.storage_bytes": 100,
        },
    )
    return user, CommercialAccess(user.id, resolved)


@contextmanager
def _commercial_context(monkeypatch, root: Path, *, active: bool = True, values=None):
    user, access = _resolved(root=root, active=active, values=values)
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.get_commercial_runtime",
        lambda: _Runtime(),
    )
    user_token = set_current_user(user)
    access_token = set_commercial_access(access)
    try:
        yield user
    finally:
        reset_commercial_access(access_token)
        reset_current_user(user_token)


def _upload(name: str = "demo.txt", data: bytes = b"hello") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(data))


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: knowledge.create_knowledge_base(
            BackgroundTasks(), "new-kb", [_upload()], "llamaindex", None
        ),
        lambda: knowledge.upload_files("existing", BackgroundTasks(), [_upload()], None, None),
        lambda: knowledge.reindex_knowledge_base("existing", BackgroundTasks()),
        lambda: knowledge.retry_knowledge_base("existing", BackgroundTasks()),
        lambda: knowledge.sync_folder("existing", "folder-1", BackgroundTasks(), None),
    ],
)
def test_consuming_kb_routes_return_stable_402_before_touching_storage(
    invoke, monkeypatch, tmp_path: Path
) -> None:
    touched = False

    def _must_not_touch_manager():
        nonlocal touched
        touched = True
        raise AssertionError("storage lookup happened before the commercial gate")

    monkeypatch.setattr(knowledge, "get_kb_manager", _must_not_touch_manager)
    monkeypatch.setattr(knowledge, "_writable_kb", lambda *_args: _must_not_touch_manager())

    with _commercial_context(monkeypatch, tmp_path / "owner", active=False):
        with pytest.raises(HTTPException) as denied:
            asyncio.run(invoke())

    assert denied.value.status_code == 402
    assert denied.value.detail["code"] == "trial_expired"
    assert touched is False
    assert not (tmp_path / "owner").exists()


def test_upload_limits_fail_closed_for_unknown_and_oversized_files(
    monkeypatch, tmp_path: Path
) -> None:
    owner_root = tmp_path / "owner"
    values = {
        "limits.kb_count": 2,
        "limits.upload_bytes": 4,
        "limits.storage_bytes": 100,
    }
    with _commercial_context(monkeypatch, owner_root, values=values):
        with pytest.raises(CommercialResourceLimitDenied) as oversized:
            enforce_upload_storage_limits([_upload(data=b"12345")])
        assert oversized.value.code == "upload_size_limit_exceeded"

        opaque = _upload()
        opaque.file.close()
        with pytest.raises(CommercialResourceLimitDenied) as unknown:
            enforce_upload_storage_limits([opaque])
        assert unknown.value.code == "upload_size_unknown"

    assert not owner_root.exists()


def test_oversized_create_is_rejected_before_manager_or_directory_lookup(
    monkeypatch, tmp_path: Path
) -> None:
    owner_root = tmp_path / "owner"
    values = {
        "limits.kb_count": 2,
        "limits.upload_bytes": 4,
        "limits.storage_bytes": 100,
    }
    touched = False

    def _must_not_touch_manager():
        nonlocal touched
        touched = True
        raise AssertionError("manager lookup happened before upload limit validation")

    monkeypatch.setattr(knowledge, "get_kb_manager", _must_not_touch_manager)
    with _commercial_context(monkeypatch, owner_root, values=values):
        with pytest.raises(HTTPException) as denied:
            asyncio.run(
                knowledge.create_knowledge_base(
                    BackgroundTasks(),
                    "new-kb",
                    [_upload(data=b"12345")],
                    "llamaindex",
                    None,
                )
            )

    assert denied.value.status_code == 402
    assert denied.value.detail["code"] == "upload_size_limit_exceeded"
    assert touched is False
    assert not owner_root.exists()


def test_storage_limit_counts_actual_owner_tree_plus_incoming_batch(
    monkeypatch, tmp_path: Path
) -> None:
    owner_root = tmp_path / "owner"
    existing = owner_root / "knowledge_bases" / "kb" / "raw" / "old.txt"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"12345678")
    values = {
        "limits.kb_count": 2,
        "limits.upload_bytes": 10,
        "limits.storage_bytes": 10,
    }
    with _commercial_context(monkeypatch, owner_root, values=values):
        with pytest.raises(CommercialResourceLimitDenied) as denied:
            enforce_upload_storage_limits([_upload(data=b"abc")])

    assert denied.value.code == "storage_limit_exceeded"
    assert denied.value.details == {"current_bytes": 8, "incoming_bytes": 3, "limit": 10}


def test_kb_count_limit_uses_registered_owner_kbs(monkeypatch, tmp_path: Path) -> None:
    values = {
        "limits.kb_count": 2,
        "limits.upload_bytes": 10,
        "limits.storage_bytes": 100,
    }
    with _commercial_context(monkeypatch, tmp_path / "owner", values=values):
        with pytest.raises(CommercialResourceLimitDenied) as denied:
            enforce_kb_count_limit(2)
        enforce_kb_count_limit(1)

    assert denied.value.code == "kb_count_limit_exceeded"


def test_owner_resource_lock_serializes_commercial_mutations(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def worker(name: str) -> None:
        async with commercial_owner_resource_lock():
            events.append(f"{name}:enter")
            if name == "first":
                first_entered.set()
                await release_first.wait()
            events.append(f"{name}:exit")

    async def scenario() -> None:
        first = asyncio.create_task(worker("first"))
        await first_entered.wait()
        second = asyncio.create_task(worker("second"))
        await asyncio.sleep(0)
        assert events == ["first:enter"]
        release_first.set()
        await asyncio.gather(first, second)

    with _commercial_context(monkeypatch, tmp_path / "owner"):
        asyncio.run(scenario())

    assert events == ["first:enter", "first:exit", "second:enter", "second:exit"]


def test_expired_background_initialization_never_enters_provider_work(
    monkeypatch, tmp_path: Path
) -> None:
    called = False

    class _Progress:
        task_id = None

        def update(self, *_args, **_kwargs) -> None:
            return None

    class _Initializer:
        kb_name = "queued-kb"
        base_dir = tmp_path / "owner" / "knowledge_bases"
        raw_dir = base_dir / kb_name / "raw"
        progress_tracker = _Progress()

        async def process_documents(self) -> None:
            nonlocal called
            called = True

    class _Manager:
        def update_kb_status(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(knowledge, "get_kb_manager", lambda: _Manager())
    with _commercial_context(monkeypatch, tmp_path / "owner", active=False):
        asyncio.run(knowledge.run_initialization_task(_Initializer(), "expired-task"))

    assert called is False


def test_llamaindex_growth_is_rejected_before_staged_index_is_promoted(
    monkeypatch, tmp_path: Path
) -> None:
    from deeptutor.services.rag.pipelines.llamaindex import storage as llama_storage

    owner_root = tmp_path / "owner"
    raw_file = owner_root / "knowledge_bases" / "kb" / "raw" / "source.txt"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"1234")
    target = owner_root / "knowledge_bases" / "kb" / "version-1"
    values = {
        "limits.kb_count": 2,
        "limits.upload_bytes": 10,
        "limits.storage_bytes": 10,
    }

    class _Index:
        pass

    def _persist_large_index(_documents, staging: Path, *, show_progress: bool):
        del show_progress
        (staging / "docstore.json").write_bytes(b"12345678")
        return _Index(), 1

    monkeypatch.setattr(
        llama_storage.ingestion,
        "create_index_from_documents",
        _persist_large_index,
    )
    monkeypatch.setattr(
        llama_storage.retrievers,
        "persist_bm25_retriever",
        lambda *_args, **_kwargs: False,
    )

    with _commercial_context(monkeypatch, owner_root, values=values):
        with pytest.raises(CommercialResourceLimitDenied) as denied:
            llama_storage.create_index([object()], target)

    assert denied.value.code == "storage_limit_exceeded"
    assert not target.exists()
    assert not list(target.parent.glob(".version-1.staging-*"))
