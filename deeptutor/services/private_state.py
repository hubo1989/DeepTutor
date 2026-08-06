"""Cross-process locking and restrictive permissions for private runtime state."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys
import threading

_thread_locks_guard = threading.Lock()
_thread_locks: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.RLock())


def chmod_private(path: Path, *, directory: bool = False) -> None:
    """Best-effort owner-only permissions without following a symlink."""
    if not path.exists() or path.is_symlink():
        return
    mode = stat.S_IRWXU if directory else stat.S_IRUSR | stat.S_IWUSR
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        os.chmod(path, mode)


def ensure_private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"Unsafe private state directory: {path}")
    chmod_private(path, directory=True)
    return path


def ensure_private_file(path: Path) -> Path:
    """Create an owner-only regular file without ever following a symlink."""
    ensure_private_directory(path.parent)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f"Unsafe private state file: {path}")
    flags = os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    os.close(descriptor)
    chmod_private(path)
    return path


def harden_sqlite_files(path: Path) -> None:
    """Restrict the database and any WAL/SHM sidecars created by SQLite."""
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        chmod_private(candidate)


@contextmanager
def exclusive_path_lock(path: Path) -> Iterator[None]:
    """Serialize a file mutation across threads and worker processes.

    The lock lives beside the protected file, so every writer and scrubber can
    share it without keeping the data file open across an atomic replacement.
    """
    directory = ensure_private_directory(path.parent)
    lock_path = directory / f".{path.name}.lock"
    lock = _thread_lock(lock_path)
    with lock, lock_path.open("a+b") as handle:
        chmod_private(lock_path)
        if sys.platform == "win32":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "chmod_private",
    "ensure_private_directory",
    "ensure_private_file",
    "exclusive_path_lock",
    "harden_sqlite_files",
]
