"""Server-side model grant resolution and redacted model views.

Grants carry LLM assignments only (grant v2): embedding and search always
resolve from the deployment's active profiles, so per-user grants for them
were never enforced and are not stored.

Three sources reach an ordinary user, and :func:`redacted_model_access` is the
one place all three are resolved:

* ``admin`` models assigned to the user through a grant;
* ``personal`` owner-bound profiles the user signed in for themselves (see
  :mod:`deeptutor.multi_user.personal_models`) — OAuth providers such as Codex
  authenticate one individual's plan, so those profiles are never lent through
  grants and are only ever surfaced to the owner; and
* ``byok`` owner-bound API-key profiles the user stored in the BYOK vault (see
  :mod:`deeptutor.multi_user.byok_vault`), gated by the deployment's BYOK
  policy and the user's grant.

Everything downstream — the option list, the capability gate, and selection
validation — reads that one function, so the three can never disagree about
what a user may use.
"""

from __future__ import annotations

from typing import Any

from deeptutor.services.config.model_catalog import ModelCatalogService
from deeptutor.services.model_selection import list_llm_options

from .byok_policy import allowed_binding, byok_runtime_enabled, grant_service_enabled, load_policy
from .byok_vault import get_user_byok_vault
from .context import get_current_user
from .grants import load_grant
from .paths import get_admin_path_service


def admin_catalog_service() -> ModelCatalogService:
    return ModelCatalogService(path=get_admin_path_service().get_settings_file("model_catalog"))


def admin_catalog() -> dict[str, Any]:
    return admin_catalog_service().load()


def _profile_by_id(catalog: dict[str, Any], service: str, profile_id: str) -> dict[str, Any] | None:
    for profile in catalog.get("services", {}).get(service, {}).get("profiles", []) or []:
        if str(profile.get("id") or "") == profile_id:
            return profile
    return None


def _model_by_id(profile: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    for model in profile.get("models", []) or []:
        if str(model.get("id") or "") == model_id:
            return model
    return None


#: Bindings whose credential is one person's own subscription login rather than
#: a billable team key. Codex stamps ``owner_bound`` onto the managed profile it
#: publishes, but a profile can also be created by hand in the settings editor —
#: a CodeBuddy profile is, and it reads the operator's own IDE-plugin session —
#: and there is nowhere for such a profile to acquire the flag. Binding is the
#: durable fact, so it decides too.
OWNER_BOUND_BINDINGS = frozenset({"openai_codex", "codebuddy"})


def is_owner_bound(profile: dict[str, Any]) -> bool:
    """Whether a profile is tied to the identity of the operator who set it up.

    OAuth providers such as Codex authenticate one individual's plan rather than
    a billable team key, so those profiles are never lent to other accounts
    through grants — each user signs in for themselves or goes without.
    """
    binding = str(profile.get("binding") or "").strip().lower()
    if binding in OWNER_BOUND_BINDINGS:
        return True
    return bool(profile.get("owner_bound"))


def redacted_model_access(user_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    user = get_current_user()
    if user_id is None:
        user_id = user.id
    grant = load_grant(user_id)
    catalog = admin_catalog()
    result: dict[str, list[dict[str, Any]]] = {"llm": []}
    for item in grant.get("models", {}).get("llm", []) or []:
        profile_id = str(item.get("profile_id") or item.get("id") or "")
        profile = _profile_by_id(catalog, "llm", profile_id)
        if profile is not None and is_owner_bound(profile):
            # A grant may predate the profile becoming owner-bound. Drop it here,
            # the one place every caller resolves grants through, so the option
            # list, the capability gate, and selection validation all agree.
            continue
        if not profile:
            result["llm"].append(
                {
                    "profile_id": profile_id,
                    "name": item.get("name") or profile_id or "Unavailable profile",
                    "source": "admin",
                    "available": False,
                }
            )
            continue
        for model_id in item.get("model_ids") or []:
            model = _model_by_id(profile, str(model_id))
            result["llm"].append(
                {
                    "profile_id": profile_id,
                    "model_id": str(model_id),
                    "name": (model or {}).get("name") or str(model_id),
                    "model": (model or {}).get("model") or "",
                    "source": "admin",
                    "available": model is not None,
                }
            )
    policy = load_policy()
    # ``user_id`` may name a user an administrator is inspecting.  Availability
    # must always reflect that target user's grant, never the viewer's role.
    byok_granted = grant_service_enabled(grant, "byok", "llm")
    byok_enabled = byok_runtime_enabled("llm", policy) and byok_granted
    for profile in get_user_byok_vault().list_profiles(user_id, service="llm"):
        provider = str(profile.get("provider") or "").strip()
        configured = bool(profile.get("configured"))
        allowed = configured and allowed_binding("llm", provider, policy)
        result["llm"].append(
            {
                "source": "byok",
                "profile_id": profile.get("id"),
                "generation": profile.get("generation"),
                "name": profile.get("name") or profile.get("model") or profile.get("id"),
                "model": profile.get("model") or "",
                "provider": provider,
                "available": bool(byok_enabled and allowed),
            }
        )
    if user_id == user.id:
        # Only ever the caller's OWN personal models. An administrator
        # inspecting somebody's grants asks for that user's id, and their
        # personal sign-in is not the administrator's business — nor is it in
        # the grant editor's gift to assign.
        from .personal_models import personal_llm_rows

        result["llm"].extend(personal_llm_rows())
    return result


def allowed_llm_options() -> dict[str, Any]:
    user = get_current_user()
    if user.is_admin:
        return list_llm_options(admin_catalog())
    access = redacted_model_access(user.id).get("llm", [])
    options = [
        {
            "profile_id": item.get("profile_id"),
            "model_id": item.get("model_id"),
            "profile_name": item.get("name") or item.get("profile_id") or "LLM",
            "model_name": item.get("name") or item.get("model") or item.get("model_id"),
            "label": item.get("name") or item.get("model") or item.get("model_id"),
            "model": item.get("model") or "",
            "provider": "",
            "source": item.get("source") or "admin",
            "is_active_default": False,
        }
        for item in access
        if item.get("source") != "byok" and item.get("available")
    ]
    options.extend(
        {
            "source": "byok",
            "profile_id": item.get("profile_id"),
            "generation": item.get("generation"),
            "profile_name": item.get("name") or item.get("profile_id") or "BYOK LLM",
            "model_name": item.get("name") or item.get("model") or "BYOK LLM",
            "label": item.get("name") or item.get("model") or item.get("profile_id"),
            "model": item.get("model") or "",
            "provider": item.get("provider") or "",
            "is_active_default": False,
        }
        for item in access
        if item.get("source") == "byok" and item.get("available")
    )
    return {"active": None, "options": options}


def has_capability_access(capability: str, user_id: str | None = None) -> bool:
    """Whether the user has at least one usable model for ``capability``.

    Admins are never gated — they manage the catalog directly. For ordinary
    users this mirrors exactly what ``redacted_model_access`` exposes to the
    frontend, so the server-side gate and the UI lock always agree.
    """
    user = get_current_user()
    if user.is_admin:
        return True
    if user_id is None:
        user_id = user.id
    items = redacted_model_access(user_id).get(capability, []) or []
    return any(item.get("available") for item in items)


def apply_allowed_llm_selection(selection: dict[str, Any] | None) -> dict[str, Any] | None:
    """Allow only authorized platform or owner-scoped BYOK references."""
    user = get_current_user()
    if not selection:
        return selection
    source = str(selection.get("source") or "platform").strip().lower()
    if source == "byok":
        if not user.is_admin:
            grant = load_grant(user.id)
            if not grant_service_enabled(grant, "byok", "llm"):
                raise PermissionError("BYOK is not enabled for this account")
        policy = load_policy()
        if not byok_runtime_enabled("llm", policy):
            raise PermissionError("BYOK is disabled for LLM requests")
        profile_id = str(selection.get("profile_id") or "").strip()
        profile = next(
            (
                item
                for item in get_user_byok_vault().list_profiles(user.id, service="llm")
                if str(item.get("id") or "") == profile_id and item.get("configured")
            ),
            None,
        )
        if profile is None or not allowed_binding(
            "llm", str(profile.get("provider") or ""), policy
        ):
            raise PermissionError("This BYOK profile is not available to your account")
        raw_generation = selection.get("generation")
        if raw_generation is None:
            raw_generation = selection.get("profile_generation")
        if raw_generation not in (None, ""):
            try:
                generation = int(raw_generation)
            except (TypeError, ValueError) as exc:
                raise PermissionError("The selected BYOK profile generation is invalid") from exc
            if generation != int(profile.get("generation") or 0):
                raise PermissionError(
                    "The selected BYOK profile changed; reload and select it again"
                )
        return {
            "source": "byok",
            "profile_id": profile_id,
            "generation": int(profile.get("generation") or 0),
        }
    if source not in ("platform", "personal"):
        raise PermissionError("Execution source must be platform, personal, or byok")
    # ``personal`` models are owner-bound OAuth sign-ins (e.g. Codex); validate
    # them exactly like a platform grant — by profile/model id against the
    # redacted access list, which already carries the personal rows.
    if user.is_admin:
        return selection
    profile_id = str(selection.get("profile_id") or "")
    model_id = str(selection.get("model_id") or "")
    for item in redacted_model_access(user.id).get("llm", []):
        if item.get("profile_id") == profile_id and item.get("model_id") == model_id:
            return selection
    raise PermissionError("This model is not assigned to your account.")


def default_llm_selection() -> dict[str, Any] | None:
    """Choose the user's default source without exposing provider secrets."""
    user = get_current_user()
    if user.is_admin:
        return None

    from .execution_source import resolve_execution_source

    vault = get_user_byok_vault()
    profiles = [
        item for item in vault.list_profiles(user.id, service="llm") if item.get("configured")
    ]
    preferences = vault.get_preferences(user.id)
    preference = preferences.get("llm") if isinstance(preferences.get("llm"), dict) else {}
    requested_source = str(preference.get("source") or "").strip().lower()
    policy = load_policy()
    valid_profiles = [
        item for item in profiles if allowed_binding("llm", str(item.get("provider") or ""), policy)
    ]
    if requested_source == "byok" or (
        not requested_source
        and byok_runtime_enabled("llm", policy)
        and grant_service_enabled(load_grant(user.id), "byok", "llm")
        and valid_profiles
    ):
        source = resolve_execution_source(
            "llm",
            preferences=preferences,
            byok_profiles=valid_profiles,
        )
        if source.is_byok:
            return {
                "source": "byok",
                "profile_id": source.profile_id,
                "generation": source.profile_generation,
            }

    for item in redacted_model_access(user.id).get("llm", []):
        if item.get("source") != "byok" and item.get("available"):
            return {
                "source": "platform",
                "profile_id": item.get("profile_id"),
                "model_id": item.get("model_id"),
            }
    raise PermissionError("No LLM model is assigned to your account.")
