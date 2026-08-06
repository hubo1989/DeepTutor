from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from deeptutor.commercial.entitlement_context import (
    CommercialAccess,
    reset_commercial_access,
    set_commercial_access,
)
from deeptutor.commercial.models import ResolvedEntitlements, SubscriptionStatus
from deeptutor.commercial.storage_limits import (
    CommercialResourceLimitDenied,
    atomic_write_bytes_with_storage_limits,
    create_staging_directory,
    promote_staged_directory_with_storage_limits,
)
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.storage.attachment_store import LocalDiskAttachmentStore


@contextmanager
def _trial_storage_context(owner_root: Path, monkeypatch):
    now = datetime.now(timezone.utc)
    resolved = ResolvedEntitlements(
        customer_id="cus-storage",
        subscription_id="sub-storage",
        plan_version_id="plan-storage",
        status=SubscriptionStatus.TRIALING,
        active=True,
        resolved_at=now,
        valid_until=now + timedelta(days=1),
        values={
            "limits.upload_bytes": 4,
            "limits.storage_bytes": 6,
        },
    )
    user = CurrentUser(
        id="u-storage",
        username="storage@example.com",
        role="user",
        scope=UserScope(kind="user", user_id="u-storage", root=owner_root),
    )
    runtime = SimpleNamespace(settings=SimpleNamespace(enabled=True))
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.get_commercial_runtime",
        lambda: runtime,
    )
    user_token = set_current_user(user)
    access_token = set_commercial_access(CommercialAccess(owner_id=user.id, resolved=resolved))
    try:
        yield
    finally:
        reset_commercial_access(access_token)
        reset_current_user(user_token)


@pytest.mark.asyncio
async def test_attachment_store_enforces_trial_file_and_total_storage_limits(
    tmp_path: Path, monkeypatch
) -> None:
    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    store = LocalDiskAttachmentStore(owner_root / "workspace/chat/attachments")

    with _trial_storage_context(owner_root, monkeypatch):
        await store.put(
            session_id="session",
            attachment_id="first",
            filename="first.bin",
            data=b"1234",
        )

        with pytest.raises(CommercialResourceLimitDenied) as oversized:
            await store.put(
                session_id="session",
                attachment_id="oversized",
                filename="oversized.bin",
                data=b"12345",
            )
        assert oversized.value.code == "upload_size_limit_exceeded"

        await store.put(
            session_id="session",
            attachment_id="second",
            filename="second.bin",
            data=b"12",
        )

        with pytest.raises(CommercialResourceLimitDenied) as full:
            await store.put(
                session_id="session",
                attachment_id="third",
                filename="third.bin",
                data=b"3",
            )
        assert full.value.code == "storage_limit_exceeded"

        # Replacing the same logical attachment does not double-count bytes.
        await store.put(
            session_id="session",
            attachment_id="first",
            filename="first.bin",
            data=b"abcd",
        )


def test_atomic_file_replacement_uses_final_delta_and_preserves_denied_target(
    tmp_path: Path, monkeypatch
) -> None:
    owner_root = tmp_path / "owner"
    target = owner_root / "workspace" / "draft.json"
    sibling = owner_root / "workspace" / "other.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"1234")
    sibling.write_bytes(b"12")

    with _trial_storage_context(owner_root, monkeypatch):
        atomic_write_bytes_with_storage_limits(target, b"abc")
        assert target.read_bytes() == b"abc"

        with pytest.raises(CommercialResourceLimitDenied) as denied:
            atomic_write_bytes_with_storage_limits(target, b"abcde")

    assert denied.value.code == "storage_limit_exceeded"
    assert target.read_bytes() == b"abc"


@pytest.mark.asyncio
async def test_concurrent_owner_commits_allow_only_one_write(tmp_path: Path, monkeypatch) -> None:
    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    barrier = threading.Barrier(2)

    def _write(name: str) -> str:
        barrier.wait(timeout=5)
        try:
            atomic_write_bytes_with_storage_limits(owner_root / name, b"1234")
        except CommercialResourceLimitDenied as exc:
            return exc.code
        return "written"

    with _trial_storage_context(owner_root, monkeypatch):
        results = await asyncio.gather(
            asyncio.to_thread(_write, "one.bin"),
            asyncio.to_thread(_write, "two.bin"),
        )

    assert sorted(results) == ["storage_limit_exceeded", "written"]
    assert sum(path.stat().st_size for path in owner_root.glob("*.bin")) == 4


def test_rejected_directory_promotion_cleans_staging_and_keeps_target_absent(
    tmp_path: Path, monkeypatch
) -> None:
    owner_root = tmp_path / "owner"
    existing = owner_root / "workspace" / "existing.bin"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"1234")
    target = owner_root / "knowledge_bases" / "kb" / "version-1"

    with _trial_storage_context(owner_root, monkeypatch):
        staging = create_staging_directory(target)
        (staging / "index.bin").write_bytes(b"5678")
        with pytest.raises(CommercialResourceLimitDenied) as denied:
            promote_staged_directory_with_storage_limits(staging, target)

    assert denied.value.code == "storage_limit_exceeded"
    assert not staging.exists()
    assert not target.exists()
    assert not list(target.parent.glob(".*.staging-*"))


def test_directory_promotion_rejects_symlink_without_leaving_staging(
    tmp_path: Path, monkeypatch
) -> None:
    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    target = owner_root / "knowledge_bases" / "kb" / "version-1"

    with _trial_storage_context(owner_root, monkeypatch):
        staging = create_staging_directory(target)
        (staging / "link.bin").symlink_to(owner_root / "missing.bin")
        with pytest.raises(CommercialResourceLimitDenied) as denied:
            promote_staged_directory_with_storage_limits(staging, target)

    assert denied.value.code == "storage_size_unknown"
    assert not staging.exists()
    assert not target.exists()
