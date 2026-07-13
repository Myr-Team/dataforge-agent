from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping

try:
    from .identity import is_trusted_tenant_identity, public_actor
except ImportError:
    from identity import is_trusted_tenant_identity, public_actor


INVITATION_STATES = ("pending", "accepted", "expired", "failed", "revoked")
_LEGAL_TRANSITIONS = {
    "pending": {"accepted", "expired", "failed", "revoked"},
    "accepted": {"revoked"},
    "expired": set(),
    "failed": set(),
    "revoked": set(),
}
_LOCK = threading.RLock()
_WORKSPACE_LOCKS: dict[str, threading.RLock] = {}


class InvitationTransitionError(ValueError):
    pass


@contextmanager
def workspace_invitation_lock(workspace_id: str) -> Iterator[None]:
    key = str(workspace_id or "").strip()
    with _LOCK:
        lock = _WORKSPACE_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


def create_pending_invitation(
    meta: dict[str, Any],
    workspace_id: str,
    *,
    email: str,
    role: str,
    invited_by: Mapping[str, Any] | None,
    provider: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one pending event, reusing an equivalent pending invitation on retries."""
    clean_email = _clean(email).lower()
    clean_role = _clean(role).lower()
    if not clean_email or "@" not in clean_email:
        raise InvitationTransitionError("invitation email is required")
    if not clean_role:
        raise InvitationTransitionError("invitation role is required")
    with workspace_invitation_lock(workspace_id):
        for invitation_id, event in _latest_events(meta).items():
            if event["state"] == "pending" and event.get("email") == clean_email and event.get("role") == clean_role:
                return dict(event)
        invitation_id = uuid.uuid4().hex
        event = _event(
            invitation_id,
            "pending",
            email=clean_email,
            role=clean_role,
            invited_by=invited_by,
            provider=provider,
        )
        _events(meta).append(event)
        return dict(event)


def transition_invitation(
    meta: dict[str, Any],
    invitation_id: str,
    state: str,
    *,
    identity: Mapping[str, Any] | None = None,
    provider: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a legal state change and treat an identical retry as idempotent."""
    target_state = _clean(state).lower()
    if target_state not in INVITATION_STATES or target_state == "pending":
        raise InvitationTransitionError("invalid invitation state")
    invitation_key = _clean(invitation_id)
    with _LOCK:
        latest = _latest_events(meta).get(invitation_key)
        if latest is None:
            raise InvitationTransitionError("invitation not found")
        clean_identity = _trusted_identity(identity) if target_state == "accepted" else {}
        if target_state == "accepted" and not clean_identity:
            raise InvitationTransitionError("accepted invitation requires trusted oid and tenant id")
        if latest["state"] == target_state:
            if target_state != "accepted" or latest.get("accepted_identity") == clean_identity:
                return dict(latest)
            raise InvitationTransitionError("accepted invitation identity cannot change")
        if target_state not in _LEGAL_TRANSITIONS.get(str(latest["state"]), set()):
            raise InvitationTransitionError(f"cannot transition invitation from {latest['state']} to {target_state}")
        event = _event(
            invitation_key,
            target_state,
            email=str(latest.get("email") or ""),
            role=str(latest.get("role") or ""),
            invited_by=latest.get("invited_by"),
            accepted_identity=clean_identity,
            provider=provider or latest.get("provider"),
        )
        _events(meta).append(event)
        return dict(event)


def accepted_invitation_for_actor(meta: Mapping[str, Any], actor: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a currently accepted invitation only for its exact trusted identity."""
    identity = _trusted_identity(actor)
    if not identity:
        return None
    for event in _latest_events(meta).values():
        if event.get("state") == "accepted" and event.get("accepted_identity") == identity:
            return dict(event)
    return None


def _events(meta: dict[str, Any]) -> list[dict[str, Any]]:
    events = meta.get("workspace_invitation_events")
    if not isinstance(events, list):
        events = []
        meta["workspace_invitation_events"] = events
    return events


def _latest_events(meta: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    events = meta.get("workspace_invitation_events") if isinstance(meta, Mapping) else []
    latest: dict[str, dict[str, Any]] = {}
    if not isinstance(events, list):
        return latest
    for raw in events:
        if not isinstance(raw, Mapping):
            continue
        invitation_id = _clean(raw.get("invitation_id"))
        state = _clean(raw.get("state")).lower()
        if invitation_id and state in INVITATION_STATES:
            latest[invitation_id] = dict(raw)
    return latest


def _event(
    invitation_id: str,
    state: str,
    *,
    email: str,
    role: str,
    invited_by: Mapping[str, Any] | None,
    accepted_identity: Mapping[str, Any] | None = None,
    provider: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "event_id": uuid.uuid4().hex,
        "invitation_id": invitation_id,
        "state": state,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "email": _clean(email).lower(),
        "role": _clean(role).lower(),
        "invited_by": public_actor(dict(invited_by or {})),
    }
    identity = _identity_fields(accepted_identity)
    if identity:
        event["accepted_identity"] = identity
    clean_provider = _provider(provider)
    if clean_provider:
        event["provider"] = clean_provider
    return event


def _trusted_identity(actor: Mapping[str, Any] | None) -> dict[str, str]:
    if not is_trusted_tenant_identity(actor):
        return {}
    clean = public_actor(dict(actor or {}))
    return {
        "actor_id": _clean(clean.get("actor_id")).lower(),
        "tenant_id": _clean(clean.get("tenant_id")).lower(),
    }


def _identity_fields(identity: Mapping[str, Any] | None) -> dict[str, str]:
    source = dict(identity or {}) if isinstance(identity, Mapping) else {}
    actor_id = _clean(source.get("actor_id")).lower()
    tenant_id = _clean(source.get("tenant_id")).lower()
    return {"actor_id": actor_id, "tenant_id": tenant_id} if actor_id and tenant_id else {}


def _provider(provider: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(provider or {}) if isinstance(provider, Mapping) else {}
    allowed = ("source", "invitation_id", "invited_user_id", "status", "error_code", "status_code")
    clean: dict[str, Any] = {}
    for key in allowed:
        value = source.get(key)
        if key == "status_code" and isinstance(value, int):
            clean[key] = value
        elif isinstance(value, (str, int)) and _clean(value):
            clean[key] = _clean(value)
    return clean


def _clean(value: Any) -> str:
    return str(value or "").strip()[:256]


__all__ = [
    "INVITATION_STATES",
    "InvitationTransitionError",
    "accepted_invitation_for_actor",
    "create_pending_invitation",
    "transition_invitation",
    "workspace_invitation_lock",
]
