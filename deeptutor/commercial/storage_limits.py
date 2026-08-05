"""Commercial resource limits for owner-scoped file mutations.

This module deliberately has no FastAPI dependency.  HTTP routes translate
``CommercialResourceLimitDenied`` into their transport-level error envelope,
while background jobs keep the domain error so they can mark the queued task
failed without pretending it was a request-validation problem.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any, AsyncIterator, Iterable, Iterator, Protocol
from uuid import uuid4
import zipfile

from deeptutor.multi_user.context import get_current_user_or_none

from . import entitlement_context


class _UploadLike(Protocol):
    filename: str | None
    file: Any


class CommercialResourceLimitDenied(PermissionError):
    """Stable plan-limit denial shared by HTTP routes and background jobs."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, int | str] | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class UploadBatchMeasurement:
    """Known incoming bytes before any owner directory is mutated."""

    file_sizes: tuple[int, ...]
    storage_bytes: int


_locks_guard = threading.Lock()
_owner_locks: dict[tuple[int, str], asyncio.Lock] = {}
_owner_commit_locks: dict[str, threading.RLock] = {}


def _commercial_limits_apply() -> bool:
    runtime = entitlement_context.get_commercial_runtime()
    if not runtime.settings.enabled:
        return False
    user = get_current_user_or_none()
    return user is None or not user.is_admin


def _current_owner() -> tuple[str, Path]:
    user = get_current_user_or_none()
    if user is None:
        raise CommercialResourceLimitDenied(
            "subscription_required",
            "An authenticated owner context is required for commercial storage.",
        )
    return user.id, user.scope.root.resolve()


@asynccontextmanager
async def commercial_owner_resource_lock() -> AsyncIterator[None]:
    """Serialize limit-check + write critical sections for one owner.

    Local/community mode and platform administrators retain their original
    concurrency behavior.  Commercial ordinary users are serialized within
    one application process; PostgreSQL-backed reservations are outside the
    Phase-2 single-instance storage boundary.
    """

    if not _commercial_limits_apply():
        yield
        return

    owner_id, _root = _current_owner()
    loop = asyncio.get_running_loop()
    key = (id(loop), owner_id)
    with _locks_guard:
        lock = _owner_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _owner_locks[key] = lock
    async with lock:
        yield


@contextmanager
def commercial_owner_commit_lock() -> Iterator[None]:
    """Serialize the final quota check and filesystem commit for one owner.

    The async route lock prevents ordinary requests in one event loop from
    racing.  Persistence workers also run in threads, so the commit boundary
    needs a process-wide synchronous lock as well.  Access is deliberately
    re-checked *after* the lock is acquired because a queued Trial may expire.
    """

    if not _commercial_limits_apply():
        yield
        return

    owner_id, _owner_root = _current_owner()
    with _locks_guard:
        lock = _owner_commit_locks.get(owner_id)
        if lock is None:
            lock = threading.RLock()
            _owner_commit_locks[owner_id] = lock
    with lock:
        entitlement_context.require_active_commercial_access()
        yield


def _known_upload_size(upload: _UploadLike) -> int:
    declared = getattr(upload, "size", None)
    if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 0:
        return declared

    stream = upload.file
    try:
        current = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(current)
    except (OSError, ValueError, AttributeError) as exc:
        raise CommercialResourceLimitDenied(
            "upload_size_unknown",
            f"The size of '{upload.filename or 'upload'}' could not be determined.",
        ) from exc
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise CommercialResourceLimitDenied(
            "upload_size_unknown",
            f"The size of '{upload.filename or 'upload'}' could not be determined.",
        )
    return size


def _materialized_upload_size(upload: _UploadLike, uploaded_size: int) -> int:
    """Account for a ZIP's extracted bytes because the archive is not retained."""

    if Path(upload.filename or "").suffix.lower() != ".zip":
        return uploaded_size
    stream = upload.file
    try:
        current = stream.tell()
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            sizes = [entry.file_size for entry in archive.infolist() if not entry.is_dir()]
        stream.seek(current)
    except zipfile.BadZipFile:
        # The existing archive validator returns the user-facing 400.  Until
        # then the compressed bytes are still a safe lower-bound measurement.
        try:
            stream.seek(current)
        except (OSError, ValueError, AttributeError):
            pass
        return uploaded_size
    except (OSError, ValueError, AttributeError) as exc:
        raise CommercialResourceLimitDenied(
            "upload_size_unknown",
            f"The materialized size of '{upload.filename or 'upload'}' could not be determined.",
        ) from exc

    if any(not isinstance(size, int) or isinstance(size, bool) or size < 0 for size in sizes):
        raise CommercialResourceLimitDenied(
            "upload_size_unknown",
            f"The materialized size of '{upload.filename or 'upload'}' could not be determined.",
        )
    return sum(sizes)


def measure_upload_batch(files: Iterable[_UploadLike]) -> UploadBatchMeasurement:
    file_sizes: list[int] = []
    materialized_total = 0
    for upload in files:
        size = _known_upload_size(upload)
        file_sizes.append(size)
        materialized_total += _materialized_upload_size(upload, size)
    return UploadBatchMeasurement(tuple(file_sizes), materialized_total)


def owner_tree_size_bytes(
    root: Path | None = None,
    *,
    excluded_roots: Iterable[str | Path] = (),
) -> int:
    """Return actual bytes below the owner root without following symlinks."""

    if root is None:
        _owner_id, root = _current_owner()
    root = Path(root)
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise CommercialResourceLimitDenied(
            "storage_size_unknown",
            "The owner storage root is not a directory.",
        )

    excluded = tuple(Path(value).resolve() for value in excluded_roots)

    def _excluded(path: Path) -> bool:
        resolved = path.resolve()
        return any(resolved == item or _is_within(resolved, item) for item in excluded)

    if _excluded(root):
        return 0

    total = 0
    pending = [root]
    try:
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    if _excluded(entry_path):
                        continue
                    if entry.is_symlink():
                        raise OSError(f"symbolic links are not supported: {entry.path}")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry_path)
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    if not entry.is_file(follow_symlinks=False):
                        raise OSError(f"unsupported storage entry: {entry.path}")
                    total += stat.st_size
    except OSError as exc:
        raise CommercialResourceLimitDenied(
            "storage_size_unknown",
            "Current owner storage could not be measured safely.",
        ) from exc
    return total


def _limit(name: str) -> int | None:
    return entitlement_context.integer_limit(name)


def _enforce_upload_sizes(file_sizes: Iterable[int]) -> None:
    upload_limit = _limit("upload_bytes")
    if upload_limit is None:
        return
    for index, size in enumerate(file_sizes):
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise CommercialResourceLimitDenied(
                "upload_size_unknown",
                "The uploaded file size could not be determined safely.",
            )
        if size > upload_limit:
            raise CommercialResourceLimitDenied(
                "upload_size_limit_exceeded",
                f"An uploaded file exceeds the plan limit of {upload_limit} bytes.",
                details={"file_index": index, "size_bytes": size, "limit": upload_limit},
            )


def enforce_kb_count_limit(current_count: int) -> None:
    limit = _limit("kb_count")
    if limit is None:
        return
    if current_count >= limit:
        raise CommercialResourceLimitDenied(
            "kb_count_limit_exceeded",
            f"The plan allows at most {limit} knowledge bases.",
            details={"current_count": current_count, "limit": limit},
        )


def _enforce_measurement(
    measurement: UploadBatchMeasurement,
    *,
    current_bytes: int,
) -> None:
    storage_limit = _limit("storage_bytes")
    _enforce_upload_sizes(measurement.file_sizes)
    if storage_limit is not None and current_bytes + measurement.storage_bytes > storage_limit:
        raise CommercialResourceLimitDenied(
            "storage_limit_exceeded",
            f"The upload would exceed the plan storage limit of {storage_limit} bytes.",
            details={
                "current_bytes": current_bytes,
                "incoming_bytes": measurement.storage_bytes,
                "limit": storage_limit,
            },
        )


def enforce_file_storage_limits(
    file_size: int,
    *,
    replacing_bytes: int = 0,
    additional_roots: Iterable[str | Path] = (),
) -> None:
    """Enforce one materialized file against upload and owner-total limits.

    ``additional_roots`` covers an explicitly configured attachment directory
    that lives outside the normal owner workspace. Callers must pass only an
    already owner-namespaced root; overlapping roots are counted once.
    """

    if (
        isinstance(file_size, bool)
        or not isinstance(file_size, int)
        or file_size < 0
        or isinstance(replacing_bytes, bool)
        or not isinstance(replacing_bytes, int)
        or replacing_bytes < 0
    ):
        raise CommercialResourceLimitDenied(
            "upload_size_unknown",
            "The uploaded file size could not be determined safely.",
        )

    _enforce_upload_sizes((file_size,))
    enforce_owner_storage_limits(
        file_size,
        replacing_bytes=replacing_bytes,
        additional_roots=additional_roots,
    )


def _current_storage_bytes(
    owner_root: Path,
    *,
    additional_roots: Iterable[str | Path] = (),
    excluded_roots: Iterable[str | Path] = (),
) -> int:
    owner_root = owner_root.resolve()
    current_bytes = owner_tree_size_bytes(owner_root, excluded_roots=excluded_roots)
    counted_roots = [owner_root]
    for value in additional_roots:
        raw_root = Path(value)
        if raw_root.is_symlink():
            raise CommercialResourceLimitDenied(
                "storage_size_unknown",
                "An additional owner storage root is a symbolic link.",
            )
        root = raw_root.resolve()
        if any(_is_within(root, counted) for counted in counted_roots):
            continue
        current_bytes += owner_tree_size_bytes(root, excluded_roots=excluded_roots)
        counted_roots.append(root)
    return current_bytes


def enforce_owner_storage_limits(
    incoming_bytes: int,
    *,
    replacing_bytes: int = 0,
    additional_roots: Iterable[str | Path] = (),
    excluded_roots: Iterable[str | Path] = (),
) -> None:
    """Enforce owner-total storage using the final replacement delta.

    Generated indexes and artifacts are not user uploads, so this helper only
    applies ``limits.storage_bytes``.  The caller must hold the owner commit
    lock while calling it and committing the measured mutation.
    """

    if (
        isinstance(incoming_bytes, bool)
        or not isinstance(incoming_bytes, int)
        or incoming_bytes < 0
        or isinstance(replacing_bytes, bool)
        or not isinstance(replacing_bytes, int)
        or replacing_bytes < 0
    ):
        raise CommercialResourceLimitDenied(
            "storage_size_unknown",
            "The storage mutation size could not be determined safely.",
        )

    storage_limit = _limit("storage_bytes")
    if storage_limit is None:
        return

    _owner_id, owner_root = _current_owner()
    current_bytes = _current_storage_bytes(
        owner_root,
        additional_roots=additional_roots,
        excluded_roots=excluded_roots,
    )
    projected_bytes = max(0, current_bytes - replacing_bytes) + incoming_bytes
    delta_bytes = incoming_bytes - replacing_bytes
    if projected_bytes > storage_limit:
        raise CommercialResourceLimitDenied(
            "storage_limit_exceeded",
            f"The write would exceed the plan storage limit of {storage_limit} bytes.",
            details={
                "current_bytes": current_bytes,
                "incoming_bytes": max(0, delta_bytes),
                "limit": storage_limit,
            },
        )


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_owner_target(target: Path, additional_roots: Iterable[str | Path]) -> None:
    _owner_id, owner_root = _current_owner()
    roots = [owner_root.resolve(), *(Path(value).resolve() for value in additional_roots)]
    resolved = target.resolve()
    if not any(resolved == root or _is_within(resolved, root) for root in roots):
        raise CommercialResourceLimitDenied(
            "storage_target_invalid",
            "The storage target is outside the authenticated owner's namespace.",
        )
    matching_root = next(root for root in roots if resolved == root or _is_within(resolved, root))
    relative = target.absolute().relative_to(matching_root)
    cursor = matching_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise CommercialResourceLimitDenied(
                "storage_target_invalid",
                "A symbolic-link storage path cannot be replaced safely.",
            )
    if target.is_symlink():
        raise CommercialResourceLimitDenied(
            "storage_target_invalid",
            "A symbolic-link storage target cannot be replaced safely.",
        )


def _atomic_replace_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=str(target.parent), delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def atomic_write_bytes_with_storage_limits(
    target: str | Path,
    data: bytes,
    *,
    enforce_upload_limit: bool = False,
    additional_roots: Iterable[str | Path] = (),
) -> None:
    """Atomically replace one file after a lock-scoped replacement-delta check."""

    target_path = Path(target)
    if not _commercial_limits_apply():
        _atomic_replace_bytes(target_path, data)
        return

    roots = tuple(additional_roots)
    with commercial_owner_commit_lock():
        _assert_owner_target(target_path, roots)
        replacing = target_path.stat().st_size if target_path.is_file() else 0
        if enforce_upload_limit:
            _enforce_upload_sizes((len(data),))
        enforce_owner_storage_limits(
            len(data),
            replacing_bytes=replacing,
            additional_roots=roots,
        )
        _atomic_replace_bytes(target_path, data)


def atomic_write_text_with_storage_limits(
    target: str | Path,
    text: str,
    *,
    additional_roots: Iterable[str | Path] = (),
) -> None:
    atomic_write_bytes_with_storage_limits(
        target,
        text.encode("utf-8"),
        additional_roots=additional_roots,
    )


def atomic_write_batch_with_storage_limits(
    files: Mapping[str | Path, bytes],
    *,
    additional_roots: Iterable[str | Path] = (),
) -> None:
    """Commit a known byte batch with rollback if any replacement fails."""

    normalized = {Path(path): bytes(data) for path, data in files.items()}
    if len(normalized) != len(files):
        raise ValueError("duplicate batch storage target")
    if not normalized:
        return
    if not _commercial_limits_apply():
        for path, data in normalized.items():
            _atomic_replace_bytes(path, data)
        return

    roots = tuple(additional_roots)
    with commercial_owner_commit_lock():
        replacing = 0
        for path in normalized:
            _assert_owner_target(path, roots)
            replacing += path.stat().st_size if path.is_file() else 0
        enforce_owner_storage_limits(
            sum(len(data) for data in normalized.values()),
            replacing_bytes=replacing,
            additional_roots=roots,
        )
        _commit_byte_batch(normalized)


def _commit_byte_batch(files: Mapping[Path, bytes]) -> None:
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, data in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("wb", dir=str(target.parent), delete=False) as handle:
                temp = Path(handle.name)
                handle.write(data)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            staged[target] = temp

        for target, temp in staged.items():
            if target.exists():
                backup = target.with_name(f".{target.name}.backup-{uuid4().hex}")
                os.replace(target, backup)
                backups[target] = backup
            os.replace(temp, target)
            committed.append(target)

        for backup in backups.values():
            backup.unlink(missing_ok=True)
    except Exception:
        for target in reversed(committed):
            target.unlink(missing_ok=True)
        for target, backup in backups.items():
            if backup.exists():
                os.replace(backup, target)
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def commit_staged_files_with_storage_limits(
    staging_root: str | Path,
    target_root: str | Path,
    *,
    upload_file_sizes: Iterable[int] = (),
) -> list[Path]:
    """Promote a staged upload tree as one rollback-capable owner mutation."""

    staging = Path(staging_root)
    target = Path(target_root)
    staged_files = _safe_staged_files(staging)
    targets = [target / path.relative_to(staging) for path in staged_files]
    try:
        if not _commercial_limits_apply():
            _commit_staged_path_batch(dict(zip(targets, staged_files, strict=True)))
            return targets

        with commercial_owner_commit_lock():
            for path in targets:
                _assert_owner_target(path, ())
            _enforce_upload_sizes(tuple(upload_file_sizes))
            replacing = sum(path.stat().st_size for path in targets if path.is_file())
            incoming = sum(path.stat().st_size for path in staged_files)
            enforce_owner_storage_limits(
                incoming,
                replacing_bytes=replacing,
                excluded_roots=(staging,),
            )
            _commit_staged_path_batch(dict(zip(targets, staged_files, strict=True)))
            return targets
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _commit_staged_path_batch(files: Mapping[Path, Path]) -> None:
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, source in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = target.with_name(f".{target.name}.backup-{uuid4().hex}")
                os.replace(target, backup)
                backups[target] = backup
            os.replace(source, target)
            committed.append(target)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    except Exception:
        for target in reversed(committed):
            target.unlink(missing_ok=True)
        for target, backup in backups.items():
            if backup.exists():
                os.replace(backup, target)
        raise
    finally:
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def create_staging_directory(target: str | Path, *, copy_existing: bool = False) -> Path:
    """Create a hidden same-filesystem staging directory beside ``target``."""

    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target_path.name}.staging-", dir=str(target_path.parent))
    )
    try:
        if copy_existing and target_path.is_dir():
            owner_tree_size_bytes(target_path)
            shutil.copytree(target_path, staging, dirs_exist_ok=True)
        return staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def promote_staged_directory_with_storage_limits(
    staging_dir: str | Path,
    target_dir: str | Path,
) -> None:
    """Quota-check then promote a complete staged directory with rollback."""

    staging = Path(staging_dir)
    target = Path(target_dir)
    backup: Path | None = None
    try:
        if staging.is_symlink() or not staging.is_dir():
            raise CommercialResourceLimitDenied(
                "storage_size_unknown",
                "The staged storage directory is missing.",
            )
        if target.is_symlink() or staging.parent.resolve() != target.parent.resolve():
            raise CommercialResourceLimitDenied(
                "storage_target_invalid",
                "The staged directory must be on the target filesystem.",
            )

        if _commercial_limits_apply():
            with commercial_owner_commit_lock():
                _assert_owner_target(target, ())
                incoming = owner_tree_size_bytes(staging)
                replacing = owner_tree_size_bytes(target) if target.is_dir() else 0
                enforce_owner_storage_limits(
                    incoming,
                    replacing_bytes=replacing,
                    excluded_roots=(staging,),
                )
                backup = _promote_directory(staging, target)
        else:
            backup = _promote_directory(staging, target)

        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    except Exception:
        if backup is not None and backup.exists():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(backup, target)
            backup = None
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def _promote_directory(staging: Path, target: Path) -> Path | None:
    backup: Path | None = None
    if target.exists():
        backup = target.with_name(f".{target.name}.backup-{uuid4().hex}")
        os.replace(target, backup)
    try:
        os.replace(staging, target)
    except Exception:
        if backup is not None and backup.exists():
            os.replace(backup, target)
        raise
    return backup


def _safe_staged_files(staging: Path) -> list[Path]:
    if staging.is_symlink() or not staging.is_dir():
        raise CommercialResourceLimitDenied(
            "storage_size_unknown",
            "The staged storage directory is missing or unsafe.",
        )
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(staging, followlinks=False):
        directory_path = Path(directory)
        for name in dirnames:
            if (directory_path / name).is_symlink():
                raise CommercialResourceLimitDenied(
                    "storage_size_unknown",
                    "The staged storage tree contains a symbolic link.",
                )
        for name in filenames:
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                raise CommercialResourceLimitDenied(
                    "storage_size_unknown",
                    "The staged storage tree contains an unsupported entry.",
                )
            files.append(path)
    return sorted(files)


def enforce_staging_scratch_limit(
    staging_dir: str | Path,
    *,
    replacing_root: str | Path | None = None,
    replacing_paths: Iterable[str | Path] = (),
) -> None:
    """Incrementally stop cooperative staging writers at the final quota ceiling.

    Third-party indexers must still be isolated by a deployment-level disk
    quota because Python cannot interrupt an arbitrary worker-thread write.
    This guard is used by upload, parsing, and subprocess paths that expose a
    cooperative checkpoint while they are producing scratch data.
    """

    if not _commercial_limits_apply():
        return
    staging = Path(staging_dir)
    with commercial_owner_commit_lock():
        incoming = owner_tree_size_bytes(staging)
        replacing = 0
        if replacing_root is not None:
            target = Path(replacing_root)
            replacing = owner_tree_size_bytes(target) if target.is_dir() else 0
        else:
            replacing = sum(
                path.stat().st_size
                for value in replacing_paths
                if (path := Path(value)).is_file() and not path.is_symlink()
            )
        enforce_owner_storage_limits(
            incoming,
            replacing_bytes=replacing,
            excluded_roots=(staging,),
        )


def enforce_upload_storage_limits(files: Iterable[_UploadLike]) -> UploadBatchMeasurement | None:
    """Validate known per-file and owner-total bytes without mutating storage."""

    upload_limit = _limit("upload_bytes")
    storage_limit = _limit("storage_bytes")
    if upload_limit is None and storage_limit is None:
        return None
    _owner_id, owner_root = _current_owner()
    measurement = measure_upload_batch(files)
    _enforce_measurement(measurement, current_bytes=owner_tree_size_bytes(owner_root))
    return measurement


def enforce_upload_size_limits(files: Iterable[_UploadLike]) -> UploadBatchMeasurement | None:
    """Validate original upload sizes without assuming every file is additive."""

    upload_limit = _limit("upload_bytes")
    if upload_limit is None:
        return None
    measurement = measure_upload_batch(files)
    _enforce_upload_sizes(measurement.file_sizes)
    return measurement


def _path_measurement(paths: Iterable[str | Path], owner_root: Path) -> UploadBatchMeasurement:
    sizes: list[int] = []
    incoming = 0
    for value in paths:
        path = Path(value)
        try:
            stat = path.stat()
        except OSError as exc:
            raise CommercialResourceLimitDenied(
                "upload_size_unknown",
                f"The size of '{path.name or path}' could not be determined.",
            ) from exc
        if not path.is_file() or stat.st_size < 0:
            raise CommercialResourceLimitDenied(
                "upload_size_unknown",
                f"The size of '{path.name or path}' could not be determined.",
            )
        sizes.append(stat.st_size)
        try:
            path.resolve().relative_to(owner_root)
        except ValueError:
            incoming += stat.st_size
    return UploadBatchMeasurement(tuple(sizes), incoming)


def enforce_path_batch_storage_limits(
    paths: Iterable[str | Path],
) -> UploadBatchMeasurement | None:
    """Validate external sync files before they are copied into owner storage."""

    upload_limit = _limit("upload_bytes")
    storage_limit = _limit("storage_bytes")
    if upload_limit is None and storage_limit is None:
        return None
    _owner_id, owner_root = _current_owner()
    measurement = _path_measurement(paths, owner_root)
    _enforce_measurement(measurement, current_bytes=owner_tree_size_bytes(owner_root))
    return measurement


__all__ = [
    "CommercialResourceLimitDenied",
    "UploadBatchMeasurement",
    "atomic_write_batch_with_storage_limits",
    "atomic_write_bytes_with_storage_limits",
    "atomic_write_text_with_storage_limits",
    "commit_staged_files_with_storage_limits",
    "commercial_owner_commit_lock",
    "commercial_owner_resource_lock",
    "create_staging_directory",
    "enforce_file_storage_limits",
    "enforce_kb_count_limit",
    "enforce_owner_storage_limits",
    "enforce_path_batch_storage_limits",
    "enforce_staging_scratch_limit",
    "enforce_upload_storage_limits",
    "enforce_upload_size_limits",
    "measure_upload_batch",
    "owner_tree_size_bytes",
    "promote_staged_directory_with_storage_limits",
]
