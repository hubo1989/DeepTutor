from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are required")
def test_auth_state_and_sqlite_sidecars_are_owner_only(
    auth_isolated_root: Path, monkeypatch
) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.services import (
        account_lifecycle,
        email_verification,
        login_rate_limit,
        password_reset,
    )
    from deeptutor.services import (
        auth as auth_service,
    )

    monkeypatch.setattr(auth_service, "AUTH_SECRET", "permission-test-secret")
    auth_service.add_user("private@example.com", "password1234")
    identity.load_or_create_auth_secret()

    connections = [
        login_rate_limit._connect(),
        password_reset._connect(),
        email_verification._open_db(),
        account_lifecycle._connect(),
    ]
    try:
        auth_dir = auth_isolated_root / "data/system/auth"
        assert stat.S_IMODE(auth_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(identity.USERS_FILE.stat().st_mode) == 0o600
        assert stat.S_IMODE(identity.SECRET_FILE.stat().st_mode) == 0o600
        sqlite_files = [
            path for path in auth_dir.iterdir() if ".sqlite3" in path.name and path.is_file()
        ]
        assert sqlite_files
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in sqlite_files)
    finally:
        for connection in connections:
            connection.close()


def test_auth_secret_persistence_failure_is_fatal_in_commercial_mode(
    auth_isolated_root: Path, monkeypatch
) -> None:
    from deeptutor.multi_user import identity

    @contextmanager
    def broken_lock(_path):
        raise OSError("read-only state")
        yield

    monkeypatch.setenv("DEEPTUTOR_COMMERCIAL_ENABLED", "true")
    monkeypatch.setattr(identity, "exclusive_path_lock", broken_lock)

    with pytest.raises(RuntimeError, match="stable authentication secret"):
        identity.load_or_create_auth_secret()
