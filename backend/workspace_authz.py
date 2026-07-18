from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

try:
    from .audit_store import AuditPersistenceError, _active_key as _audit_active_key, _actor_hash as _audit_actor_hash
    from .blob_store import upload_blob_json
    from .identity import canonical_actor_identity, default_actor, is_trusted_tenant_identity, public_actor
    from .invitation_store import InvitationPersistenceError, InvitationTransitionError, consume_accepted_invitation, current_invited_member_role
    from .workspace_store import WORKSPACES, _load_workspace_bundle
except ImportError:
    from audit_store import AuditPersistenceError, _active_key as _audit_active_key, _actor_hash as _audit_actor_hash
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
    "audit.read",
    "chargeback.read",
    "invitation.read",
    "member.manage",
    "outcome.verify",
    "workspace.delete",
}


@dataclass(frozen=True)
class WorkspaceAccessDecision:
    allowed: bool
    role: str | None
    reason_code: str


class WorkspaceAuthorizationError(PermissionError):
    def __init__(self, action: str, decision: WorkspaceAccessDecision) -> None:
        self.action = action
        self.decision = decision
        super().__init__(f"workspace access denied for {action}")


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


def with_action(decision: WorkspaceAccessDecision, action: str) -> WorkspaceAccessDecision:
    if not decision.role:
        return decision
    if authorize(decision.role, action):
        return decision
    return WorkspaceAccessDecision(False, decision.role, "role_denied")


def workspace_access_decision(workspace_id: str, actor: Mapping[str, Any] | None) -> WorkspaceAccessDecision:
    clean_actor = public_actor(dict(actor or {}))
    strict = rbac_enabled()
    if strict and not is_trusted_tenant_identity(clean_actor):
        return WorkspaceAccessDecision(False, None, "identity_missing")

    meta = _load_workspace_meta(workspace_id)
    identity = canonical_actor_identity(clean_actor)
    owner = meta.get("workspace_owner") if isinstance(meta.get("workspace_owner"), Mapping) else {}
    owner_identity = canonical_actor_identity(owner)
    if identity and identity == owner_identity:
        _normalize_owner_member(workspace_id, meta, clean_actor)
        return WorkspaceAccessDecision(True, "owner", "owner_match")
    if identity and owner_identity and identity[1] == owner_identity[1]:
        return WorkspaceAccessDecision(False, None, "tenant_mismatch")
    if not strict and _legacy_default_owner_match(clean_actor):
        return WorkspaceAccessDecision(True, "owner", "owner_match")
    return _member_access_decision(workspace_id, meta, clean_actor, strict=strict)


def workspace_role(workspace_id: str, actor: Mapping[str, Any] | None) -> str | None:
    decision = workspace_access_decision(workspace_id, actor)
    return decision.role if decision.allowed else None


def active_workspace_role(workspace_id: str, actor: Mapping[str, Any] | None) -> str | None:
    """Resolve only a current persisted membership for fail-closed governance reads."""
    clean_actor = public_actor(dict(actor or {}))
    if not is_trusted_tenant_identity(clean_actor):
        return None
    decision = workspace_access_decision(workspace_id, clean_actor)
    return decision.role if decision.allowed else None


def require_workspace_permission(
    workspace_id: str,
    actor: Mapping[str, Any] | None,
    action: str,
) -> str:
    if not rbac_enabled():
        return "compatibility"
    decision = with_action(workspace_access_decision(workspace_id, actor), action)
    if not decision.allowed:
        raise WorkspaceAuthorizationError(action, decision)
    return str(decision.role)


def require_sensitive_workspace_permission(
    workspace_id: str,
    actor: Mapping[str, Any] | None,
    action: str,
    *,
    role_resolver: Callable[[str, Mapping[str, Any] | None], str | None] | None = None,
) -> str:
    if _sensitive_local_development_bypass_enabled():
        return "local_development"
    if not is_trusted_tenant_identity(actor):
        raise WorkspaceAuthorizationError(action, WorkspaceAccessDecision(False, None, "identity_missing"))
    decision = workspace_access_decision(workspace_id, actor)
    if role_resolver is not None:
        role = role_resolver(workspace_id, actor)
        decision = WorkspaceAccessDecision(bool(role), role, decision.reason_code if not role else "member_match")
    checked = with_action(decision, action)
    if not checked.allowed:
        raise WorkspaceAuthorizationError(action, checked)
    return str(checked.role)


def _sensitive_local_development_bypass_enabled() -> bool:
    environment = str(os.environ.get("DF_ENVIRONMENT") or "").strip().lower()
    enabled = str(os.environ.get("DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return enabled and environment in {"local", "development", "test"}


def _load_workspace_meta(workspace_id: str) -> dict[str, Any]:
    bundle = _load_workspace_bundle(str(workspace_id or ""))
    if bundle is None:
        return {}
    meta = bundle[0] if isinstance(bundle, tuple) and bundle else {}
    return dict(meta) if isinstance(meta, dict) else {}


def _legacy_default_owner_match(actor: Mapping[str, Any]) -> bool:
    actor_email = str(actor.get("email") or "").strip().lower()
    owner_email = str(public_actor(default_actor()).get("email") or "").strip().lower()
    return bool(actor_email and actor_email == owner_email)


def _member_access_decision(
    workspace_id: str,
    meta: dict[str, Any],
    actor: Mapping[str, Any],
    *,
    strict: bool,
) -> WorkspaceAccessDecision:
    identity = canonical_actor_identity(actor)
    if identity is None:
        if strict:
            return WorkspaceAccessDecision(False, None, "identity_missing")
        return _legacy_member_access_decision(meta, actor)

    journal_role = current_invited_member_role(meta, workspace_id, actor)
    if journal_role is not None:
        return WorkspaceAccessDecision(True, journal_role, "member_match")

    for item in meta.get("workspace_members") or []:
        if not isinstance(item, Mapping) or item.get("invitation_id"):
            continue
        member_identity = canonical_actor_identity(item)
        if member_identity == identity:
            if str(item.get("status") or "").strip().lower() != "active":
                return WorkspaceAccessDecision(False, None, "membership_missing")
            role = _normalize_role(item.get("role"))
            return WorkspaceAccessDecision(bool(role), role, "member_match" if role else "membership_missing")
        if member_identity and member_identity[1] == identity[1]:
            return WorkspaceAccessDecision(False, None, "tenant_mismatch")

    activated_role = _activate_accepted_invitation(workspace_id, meta, actor)
    if activated_role:
        return WorkspaceAccessDecision(True, activated_role, "member_match")
    return WorkspaceAccessDecision(False, None, "membership_missing")


def _legacy_member_access_decision(meta: Mapping[str, Any], actor: Mapping[str, Any]) -> WorkspaceAccessDecision:
    actor_email = str(actor.get("email") or "").strip().lower()
    actor_id = str(actor.get("actor_id") or "").strip().lower()
    actor_tenant = str(actor.get("tenant_id") or "").strip().lower()
    owner = meta.get("workspace_owner") if isinstance(meta.get("workspace_owner"), Mapping) else {}
    if _same_actor(actor_email, actor_id, actor_tenant, owner):
        return WorkspaceAccessDecision(True, "owner", "owner_match")
    for item in meta.get("workspace_members") or []:
        if not isinstance(item, Mapping) or item.get("invitation_id") or not _same_actor(actor_email, actor_id, actor_tenant, item):
            continue
        if str(item.get("status") or "").strip().lower() != "active":
            return WorkspaceAccessDecision(False, None, "membership_missing")
        role = _normalize_role(item.get("role"))
        return WorkspaceAccessDecision(bool(role), role, "member_match" if role else "membership_missing")
    return WorkspaceAccessDecision(False, None, "membership_missing")


def _normalize_owner_member(workspace_id: str, meta: dict[str, Any], actor: Mapping[str, Any]) -> None:
    identity = canonical_actor_identity(actor)
    owner = meta.get("workspace_owner") if isinstance(meta.get("workspace_owner"), Mapping) else {}
    if not identity or not is_trusted_tenant_identity(actor) or canonical_actor_identity(owner) != identity:
        return

    members: list[dict[str, Any]] = []
    normalized = False
    changed = False
    for item in meta.get("workspace_members") or []:
        member = dict(item) if isinstance(item, Mapping) else {}
        if canonical_actor_identity(member) != identity:
            members.append(member)
            continue
        if normalized:
            changed = True
            continue
        normalized = True
        if (
            member.get("role") != "owner"
            or str(member.get("status") or "").strip().lower() != "active"
            or member.get("actor_id") != actor.get("actor_id")
            or member.get("tenant_id") != actor.get("tenant_id")
        ):
            changed = True
        member.update(
            {
                "actor_id": actor.get("actor_id"),
                "tenant_id": actor.get("tenant_id"),
                "role": "owner",
                "status": "active",
                "source": "workspace_owner",
            }
        )
        members.append(member)
    if normalized and not changed:
        return
    if not normalized:
        members.append(
            {
                "actor_id": actor.get("actor_id"),
                "tenant_id": actor.get("tenant_id"),
                "role": "owner",
                "status": "active",
                "source": "workspace_owner",
            }
        )

    normalizations = [dict(item) for item in meta.get("authorization_normalizations") or [] if isinstance(item, Mapping)][-49:]
    normalizations.append(
        {
            "kind": "owner_membership",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "identity_correlation": _identity_correlation_digest(identity),
        }
    )
    meta["workspace_members"] = members
    meta["authorization_normalizations"] = normalizations
    _save_workspace_meta(workspace_id, meta)


def _identity_correlation_digest(identity: tuple[str, str]) -> str:
    actor = {"tenant_id": identity[0], "actor_id": identity[1]}
    try:
        _, key = _audit_active_key()
        digest = _audit_actor_hash(actor, key).removeprefix("actor_")
    except AuditPersistenceError:
        digest = hashlib.sha256(f"tenant:{identity[0]}|actor:{identity[1]}".encode("utf-8")).hexdigest()[:40]
    return f"corr_{digest}"


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
    role = bootstrap_role
    members: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    activated = False
    for item in meta.get("workspace_members") or []:
        member = dict(item) if isinstance(item, Mapping) else {}
        if str(member.get("invitation_id") or "") == invitation_id:
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
    "WorkspaceAccessDecision",
    "WorkspaceAuthorizationError",
    "active_workspace_role",
    "authorize",
    "rbac_enabled",
    "require_sensitive_workspace_permission",
    "require_workspace_permission",
    "with_action",
    "workspace_access_decision",
    "workspace_role",
]
