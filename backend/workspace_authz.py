from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Mapping

try:
    from .blob_store import upload_blob_json
    from .identity import canonical_actor_identity, default_actor, is_trusted_tenant_identity, public_actor
    from .invitation_store import InvitationPersistenceError, InvitationTransitionError, consume_accepted_invitation, current_invited_member_role
    from .workspace_store import WORKSPACES, _load_workspace_bundle
except ImportError:
    from blob_store import upload_blob_json
    from identity import canonical_actor_identity, default_actor, is_trusted_tenant_identity, public_actor
    from invitation_store import InvitationPersistenceError, InvitationTransitionError, consume_accepted_invitation, current_invited_member_role
    from workspace_store import WORKSPACES, _load_workspace_bundle


WORKSPACE_ROLES = ("owner", "admin", "editor", "viewer")
_LOCK = threading.RLock()

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
    "chargeback.read",
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
    strict = rbac_enabled()
    if strict and not is_trusted_tenant_identity(clean_actor):
        return None
    actor_email = str(clean_actor.get("email") or "").strip().lower()
    actor_id = str(clean_actor.get("actor_id") or "").strip().lower()
    actor_tenant = str(clean_actor.get("tenant_id") or "").strip().lower()
    owner = public_actor(default_actor())
    if not strict and actor_email and actor_email == str(owner.get("email") or "").strip().lower():
        return "owner"

    meta = _load_workspace_meta(workspace_id)
    stored_owner = meta.get("workspace_owner") if isinstance(meta.get("workspace_owner"), Mapping) else {}
    actor_identity = canonical_actor_identity(clean_actor)
    if strict:
        if actor_identity and (canonical_actor_identity(stored_owner) == actor_identity or canonical_actor_identity(owner) == actor_identity):
            return "owner"
    elif _same_actor(actor_email, actor_id, actor_tenant, stored_owner):
        return "owner"
    journal_role = current_invited_member_role(meta, workspace_id, clean_actor)
    if journal_role is not None:
        return journal_role
    for item in meta.get("workspace_members") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("invitation_id"):
            continue
        matches = canonical_actor_identity(item) == actor_identity if strict else _same_actor(actor_email, actor_id, actor_tenant, item)
        if not matches:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status != "active":
            continue
        return _normalize_role(item.get("role"))
    activated_role = _activate_accepted_invitation(workspace_id, meta, clean_actor)
    if activated_role:
        return activated_role
    return None


def active_workspace_role(workspace_id: str, actor: Mapping[str, Any] | None) -> str | None:
    """Resolve only a current persisted membership for fail-closed governance reads."""
    if not is_trusted_tenant_identity(actor):
        return None
    actor_identity = canonical_actor_identity(actor)
    if actor_identity is None:
        return None
    meta = _load_workspace_meta(workspace_id)
    owner = meta.get("workspace_owner") if isinstance(meta.get("workspace_owner"), Mapping) else {}
    if canonical_actor_identity(owner) == actor_identity:
        return "owner"
    journal_role = current_invited_member_role(meta, workspace_id, actor)
    if journal_role is not None:
        return journal_role
    for item in meta.get("workspace_members") or []:
        if isinstance(item, Mapping) and item.get("invitation_id"):
            continue
        if not isinstance(item, Mapping) or canonical_actor_identity(item) != actor_identity:
            continue
        if str(item.get("status") or "").strip().lower() != "active":
            return None
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


def _same_actor(actor_email: str, actor_id: str, actor_tenant: str, member: Mapping[str, Any]) -> bool:
    member_id = str(member.get("actor_id") or member.get("id") or "").strip().lower()
    member_email = str(member.get("email") or "").strip().lower()
    member_tenant = str(member.get("tenant_id") or member.get("tid") or "").strip().lower()
    if member_tenant and actor_tenant != member_tenant:
        return False
    if member_id:
        return bool(actor_id and actor_id == member_id)
    return bool(actor_email and member_email and actor_email == member_email)


def _activate_accepted_invitation(
    workspace_id: str,
    meta: dict[str, Any],
    actor: Mapping[str, Any],
) -> str | None:
    if not is_trusted_tenant_identity(actor):
        return None
    try:
        accepted = consume_accepted_invitation(meta, workspace_id, actor)
    except (InvitationPersistenceError, InvitationTransitionError):
        return None
    if not accepted:
        return None
    invitation_id = str(accepted.get("invitation_id") or "")
    bootstrap_role = _normalize_role(accepted.get("role"))
    if not invitation_id or bootstrap_role is None:
        return None
    members: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    activated = False
    for item in meta.get("workspace_members") or []:
        member = dict(item) if isinstance(item, Mapping) else {}
        if str(member.get("invitation_id") or "") == invitation_id:
            role = _normalize_role(member.get("role"))
            if role is None:
                return None
            member.update(
                {
                    "status": "active",
                    "actor_id": actor.get("actor_id"),
                    "tenant_id": actor.get("tenant_id"),
                    "accepted_at": now,
                    "updated_at": now,
                }
            )
            activated = True
        members.append(member)
    if not activated:
        role = bootstrap_role
        members.append(
            {
                "email": accepted.get("email") or "",
                "role": role,
                "status": "active",
                "actor_id": actor.get("actor_id"),
                "tenant_id": actor.get("tenant_id"),
                "invitation_id": invitation_id,
                "source": "workspace_invite",
                "accepted_at": now,
                "updated_at": now,
            }
        )
    updated = dict(meta)
    updated["workspace_members"] = members
    _save_workspace_meta(workspace_id, updated)
    return role


def _save_workspace_meta(workspace_id: str, meta: Mapping[str, Any]) -> None:
    value = dict(meta)
    value["workspace_id"] = str(value.get("workspace_id") or workspace_id)
    value["updated_at"] = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        workspace_dir = WORKSPACES / workspace_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        path = workspace_dir / "workspace.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        try:
            upload_blob_json(f"workspaces/{workspace_id}/workspace.json", value)
        except Exception:
            pass


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
