"""FastAPI lifecycle integration for the commercial control plane."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
import re
from typing import Any

from deeptutor.multi_user.model_access import admin_catalog
from deeptutor.services.auth import list_users
from deeptutor.services.config import load_auth_settings, load_integrations_settings

from .errors import CommercialConfigurationError
from .runtime import CommercialRuntime, get_commercial_runtime

logger = logging.getLogger(__name__)

BOOTSTRAP_ADMIN_EMAIL_ENV = "DEEPTUTOR_BOOTSTRAP_ADMIN_EMAIL"


def validate_commercial_deployment(
    runtime: CommercialRuntime,
    *,
    environ: Mapping[str, str] | None = None,
    auth_settings: Mapping[str, Any] | None = None,
    integrations_settings: Mapping[str, Any] | None = None,
    catalog: Mapping[str, Any] | None = None,
    users: list[dict[str, Any]] | None = None,
) -> None:
    """Validate hosted-mode security invariants before opening PostgreSQL."""

    if not runtime.settings.enabled:
        return

    auth = auth_settings if auth_settings is not None else load_auth_settings()
    integrations = (
        integrations_settings if integrations_settings is not None else load_integrations_settings()
    )
    if auth.get("enabled") is not True:
        raise CommercialConfigurationError("commercial mode requires auth.enabled=true")
    if auth.get("cookie_secure") is not True:
        raise CommercialConfigurationError("commercial mode requires auth.cookie_secure=true")
    if str(integrations.get("pocketbase_url") or "").strip():
        raise CommercialConfigurationError(
            "Commercial Foundation v1 supports only the built-in identity store; "
            "PocketBase is not supported"
        )

    values = os.environ if environ is None else environ
    bootstrap_email = str(values.get(BOOTSTRAP_ADMIN_EMAIL_ENV) or "").strip().casefold()
    if len(bootstrap_email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", bootstrap_email):
        raise CommercialConfigurationError(
            "commercial mode requires a valid DEEPTUTOR_BOOTSTRAP_ADMIN_EMAIL"
        )

    local_users = list_users() if users is None else users
    if local_users:
        active_admins = [
            item
            for item in local_users
            if str(item.get("role") or "user") == "admin"
            and not bool(item.get("disabled", False))
            and item.get("email_verified") is True
        ]
        if not active_admins:
            raise CommercialConfigurationError(
                "commercial mode has existing users but no active verified administrator"
            )
        if not any(
            str(item.get("username") or "").strip().casefold() == bootstrap_email
            for item in active_admins
        ):
            raise CommercialConfigurationError(
                "DEEPTUTOR_BOOTSTRAP_ADMIN_EMAIL must match an existing active administrator"
            )

    binding = runtime.settings.trial_model_binding
    if binding is None:
        raise CommercialConfigurationError(
            "commercial mode requires an explicit Trial v1 model binding"
        )
    current_catalog = catalog if catalog is not None else admin_catalog()
    profiles = (
        current_catalog.get("services", {}).get("llm", {}).get("profiles", [])
        if isinstance(current_catalog, Mapping)
        else []
    )
    profile = next(
        (
            item
            for item in profiles
            if isinstance(item, Mapping) and str(item.get("id") or "") == binding.profile_id
        ),
        None,
    )
    if profile is None:
        raise CommercialConfigurationError(
            f"Trial v1 LLM profile does not exist in the admin catalog: {binding.profile_id}"
        )
    if bool(profile.get("owner_bound")):
        raise CommercialConfigurationError(
            "Trial v1 cannot use an owner-bound administrator model profile"
        )
    available_model_ids = {
        str(item.get("id") or "")
        for item in profile.get("models", []) or []
        if isinstance(item, Mapping)
    }
    missing_models = sorted(set(binding.model_ids) - available_model_ids)
    if missing_models:
        raise CommercialConfigurationError(
            "Trial v1 model IDs are missing from the bound admin profile: "
            + ", ".join(missing_models)
        )


async def reconcile_existing_commercial_customers(
    runtime: CommercialRuntime,
    *,
    users: list[dict[str, Any]] | None = None,
) -> int:
    """Map eligible identities without starting dormant users' trial clocks."""

    if not runtime.settings.enabled:
        return 0
    reconciled = 0
    for user in list_users() if users is None else users:
        if (
            str(user.get("role") or "user") != "user"
            or bool(user.get("disabled", False))
            or user.get("email_verified") is not True
        ):
            continue
        owner_id = str(user.get("id") or "").strip()
        if not owner_id:
            raise CommercialConfigurationError(
                "verified regular user is missing a stable identity id"
            )
        control_plane = runtime.control_plane
        if control_plane is None:
            raise RuntimeError("commercial runtime has not completed startup")
        await control_plane.ensure_billing_customer(owner_id=owner_id)
        reconciled += 1
    return reconciled


async def startup_commercial_foundation(
    runtime: CommercialRuntime | None = None,
) -> CommercialRuntime:
    """Validate, migrate, seed, and reconcile before serving requests."""

    current = runtime or get_commercial_runtime()
    if not current.settings.enabled:
        return current
    validate_commercial_deployment(current)
    try:
        await current.startup()
        control_plane = current.control_plane
        if control_plane is None:
            raise RuntimeError("commercial runtime has not completed startup")
        reconciliation = await control_plane.reconcile(repair=True)
        count = await reconcile_existing_commercial_customers(current)
    except BaseException:
        await current.shutdown()
        raise
    logger.info(
        "Commercial control plane started; reconciled %d existing users and repaired %d stale rows",
        count,
        reconciliation.repaired,
    )
    return current


async def shutdown_commercial_foundation(runtime: CommercialRuntime) -> None:
    if not runtime.settings.enabled:
        return
    await runtime.shutdown()


__all__ = [
    "reconcile_existing_commercial_customers",
    "shutdown_commercial_foundation",
    "startup_commercial_foundation",
    "validate_commercial_deployment",
]
