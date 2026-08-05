"""Public account creation never grants implicit deployment administration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def test_first_save_user_keeps_requested_user_role(mu_isolated_root):
    from deeptutor.multi_user.identity import list_user_info, save_user

    save_user("alice", "$2b$12$placeholder", role="user")
    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "user"


def test_explicit_admin_role_is_honored(mu_isolated_root):
    from deeptutor.multi_user.identity import list_user_info, save_user

    save_user("alice", "$2b$12$placeholder", role="admin")
    save_user("bob", "$2b$12$placeholder", role="user")
    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "admin"
    assert users["bob"]["role"] == "user"


def test_concurrent_first_saves_create_no_admin(mu_isolated_root):
    """Concurrent public writes cannot accidentally mint an administrator."""
    from deeptutor.multi_user.identity import list_user_info, save_user

    def _save(name):
        try:
            save_user(name, "$2b$12$placeholder", role="user")
            return True
        except Exception:
            return False

    names = [f"u{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_save, names))

    users = list_user_info()
    admins = [u for u in users if u["role"] == "admin"]
    assert admins == []
    assert len(users) == 8


def test_bootstrap_admin_requires_no_existing_admin_and_matching_email(mu_isolated_root):
    from deeptutor.multi_user.identity import create_user_if_absent

    _, owner = create_user_if_absent(
        "owner@example.com",
        "$2b$12$placeholder",
        role="user",
        bootstrap_admin_username="OWNER@example.com",
    )
    _, other = create_user_if_absent(
        "other@example.com",
        "$2b$12$placeholder",
        role="user",
        bootstrap_admin_username="owner@example.com",
    )

    assert owner["role"] == "admin"
    assert other["role"] == "user"


def test_bootstrap_email_is_promoted_after_regular_account_exists(mu_isolated_root):
    from deeptutor.multi_user.identity import create_user_if_absent

    create_user_if_absent("first@example.com", "$2b$12$placeholder", role="user")
    _, owner = create_user_if_absent(
        "owner@example.com",
        "$2b$12$placeholder",
        role="user",
        bootstrap_admin_username="owner@example.com",
    )

    assert owner["role"] == "admin"


def test_bootstrap_email_is_not_promoted_when_an_admin_already_exists(mu_isolated_root):
    from deeptutor.multi_user.identity import create_user_if_absent

    create_user_if_absent("existing-admin", "$2b$12$placeholder", role="admin")
    _, owner = create_user_if_absent(
        "owner@example.com",
        "$2b$12$placeholder",
        role="user",
        bootstrap_admin_username="owner@example.com",
    )

    assert owner["role"] == "user"
