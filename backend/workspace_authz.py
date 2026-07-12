from __future__ import annotations

import os
from typing import Any, Mapping

try:
    from .identity import default_actor, public_actor
    from .workspace_store import _load_workspace_bundle
except ImportError:
    from identity import default_actor, public_actor
    from workspace_store import _load_workspace_bundle


WORKSPACE_ROLES = ("owner", "admin", "editor", "viewer")

_READ_ACTIONS = {
    "workspace.read",
    "file.read",
    "run.read",
    "artifact.read",
    "member.read",
    "outcome.read",
}
_EDITOR_ACTIONS = {
    "file.create",
    "file.edit",
    "file.delete",
    "analysis.run",
    "connector.manage",
    "outcome.record",
    "artifact.generate",
}
_ADMIN_ACTIONS = {
    "member.manage",
    "outcome.verify",
    "workspace.delete",
}


def rbac_enabled() -> bool:
    return str(os.environ.get("DF_WORKSPACE_RBAC_ENFORCED") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def authorize(role: str | None, action: str) -> bool:
    normalized_role = _normalize_role(role)
    normalized_action = str(action or "").strip().lower()
    if normalized_role is None or not normalized_action:
        return False
    if normalized_action in _READ_ACTIONS:
        return True
    if normalized_action in _EDITOR_ACTIONS:
        return normalized_role in {"owner", "admin", "editor"}
    if normalized_action in _ADMIN_ACTIONS:
        return normalized_role in {"owner", "admin"}
    return False


def workspace_role(workspace_id: str, actor: Mapping[str, Any] | None) -> str | None:
    clean_actor = public_actor(dict(actor or {}))
    actor_email = str(clean_actor.get("email") or "").strip().lower()
    actor_id = str(clean_actor.get("actor_id") or "").strip().lower()
    owner = public_actor(default_actor())
    if actor_email and actor_email == str(owner.get("email") or "").strip().lower():
        return "owner"

    meta = _load_workspace_meta(workspace_id)
    stored_owner = meta.get("workspace_owner") if isinstance(meta.get("workspace_owner"), Mapping) else {}
    if _same_actor(actor_email, actor_id, stored_owner):
        return "owner"
    for item in meta.get("workspace_members") or []:
        if not isinstance(item, Mapping) or not _same_actor(actor_email, actor_id, item):
            continue
        return _normalize_role(item.get("role"))
    return None


def require_workspace_permission(
    workspace_id: str,
    actor: Mapping[str, Any] | None,
    action: str,
) -> str:
    if not rbac_enabled():
        return "compatibility"
    role = workspace_role(workspace_id, actor)
    if not authorize(role, action):
        raise PermissionError(f"workspace permission denied for {action}")
    return str(role)


def _load_workspace_meta(workspace_id: str) -> dict[str, Any]:
    bundle = _load_workspace_bundle(str(workspace_id or ""))
    if bundle is None:
        return {}
    meta = bundle[0] if isinstance(bundle, tuple) and bundle else {}
    return dict(meta) if isinstance(meta, dict) else {}


def _same_actor(actor_email: str, actor_id: str, member: Mapping[str, Any]) -> bool:
    member_id = str(member.get("actor_id") or member.get("id") or "").strip().lower()
    member_email = str(member.get("email") or "").strip().lower()
    return bool((actor_id and member_id and actor_id == member_id) or (actor_email and member_email and actor_email == member_email))


def _normalize_role(value: Any) -> str | None:
    role = str(value or "").strip().lower()
    return role if role in WORKSPACE_ROLES else None


__all__ = [
    "WORKSPACE_ROLES",
    "authorize",
    "rbac_enabled",
    "require_workspace_permission",
    "workspace_role",
]
