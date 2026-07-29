from __future__ import annotations

import copy
import hashlib
import hmac
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, TypeVar

try:
    from .blob_store import blob_configured, compare_and_swap_blob_json, download_blob_json
    from .identity import is_trusted_tenant_identity, public_actor
except ImportError:
    from blob_store import blob_configured, compare_and_swap_blob_json, download_blob_json
    from identity import is_trusted_tenant_identity, public_actor


INVITATION_STATES = ("pending", "accepted", "expired", "failed", "revoked")
INVITATION_ROLES = ("admin", "editor", "viewer")
_LEGAL_TRANSITIONS = {
    "pending": {"accepted", "expired", "failed", "revoked"},
    "accepted": {"revoked"},
    "expired": set(),
    "failed": set(),
    "revoked": set(),
}
_LOCK = threading.RLock()
_WORKSPACE_LOCKS: dict[str, threading.RLock] = {}
_MAX_CAS_RETRIES = 5
T = TypeVar("T")


class InvitationTransitionError(ValueError):
    pass


class InvitationPersistenceError(RuntimeError):
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
    reissue: bool = False,
) -> dict[str, Any]:
    clean_email = _email(email)
    clean_role = _role(role)

    def mutate(value: dict[str, Any]) -> dict[str, Any]:
        latest = _latest_events(value)
        if not reissue:
            for event in latest.values():
                if event.get("state") == "pending" and event.get("email") == clean_email:
                    effective_role = _effective_role(value, str(event["invitation_id"]), event.get("role"))
                    if effective_role != clean_role:
                        raise InvitationTransitionError("pending invitation has a different effective role")
                    return {**event, "role": effective_role}
        if reissue:
            _revoke_effective(value, clean_email)
        invitation_id = uuid.uuid4().hex
        event = _state_event(invitation_id, "pending", clean_email, clean_role, invited_by, provider=provider)
        _events(value).append(event)
        return dict(event)

    return _mutate(workspace_id, meta, mutate)


def transition_invitation(
    meta: dict[str, Any],
    invitation_id: str,
    state: str,
    *,
    identity: Mapping[str, Any] | None = None,
    provider: Mapping[str, Any] | None = None,
    workspace_id: str = "",
) -> dict[str, Any]:
    target_state = _state(state)
    invitation_key = _clean(invitation_id)

    def mutate(value: dict[str, Any]) -> dict[str, Any]:
        return _transition(value, invitation_key, target_state, identity=_trusted_identity(identity), provider=provider)

    return _mutate(workspace_id, meta, mutate)


def accept_provider_invitation(
    meta: dict[str, Any],
    workspace_id: str,
    invitation_id: str,
    provider: Mapping[str, Any] | None,
    *,
    inviter: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    clean_provider = _provider(provider)
    identity = _provider_identity(provider, inviter)
    if not identity:
        return None

    def mutate(value: dict[str, Any]) -> dict[str, Any]:
        try:
            return _transition(value, _clean(invitation_id), "accepted", identity=identity, provider=clean_provider)
        except InvitationTransitionError as exc:
            if str(exc) == "invitation not found":
                return None
            raise

    return _mutate(workspace_id, meta, mutate)


def update_invited_member_role(meta: dict[str, Any], workspace_id: str, *, email: str, role: str) -> bool:
    clean_email, clean_role = _email(email), _role(role)
    def mutate(value: dict[str, Any]) -> bool:
        changed = False
        for event in _latest_events(value).values():
            if event.get("email") == clean_email and event.get("state") in {"pending", "accepted"}:
                _events(value).append({"event_id": uuid.uuid4().hex, "event_type": "role_change", "invitation_id": event["invitation_id"], "email": clean_email, "role": clean_role, "accepted_identity": event.get("accepted_identity"), "occurred_at": _now()})
                changed = True
        return changed
    return _mutate(workspace_id, meta, mutate)


def current_invited_member_role(meta: Mapping[str, Any], workspace_id: str, actor: Mapping[str, Any] | None) -> str | None:
    identity = _trusted_identity(actor)
    if not identity:
        return None
    value = _read(workspace_id, meta)
    try:
        latest, consumed = _latest_events(value), _consumed_ids(value)
    except InvitationTransitionError:
        return None
    roles = {str(event.get("invitation_id") or ""): str(event.get("role") or "") for event in value.get("workspace_invitation_events") or [] if isinstance(event, Mapping) and event.get("event_type") == "role_change"}
    for invitation_id, event in latest.items():
        if event.get("state") == "accepted" and _identity_key(event.get("accepted_identity")) == _identity_key(identity) and invitation_id in consumed:
            return _role(roles.get(invitation_id) or event.get("role"))
    return None


def revoke_effective_invitations(
    meta: dict[str, Any],
    workspace_id: str,
    *,
    email: str,
) -> list[dict[str, Any]]:
    clean_email = _email(email)
    return _mutate(workspace_id, meta, lambda value: _revoke_effective(value, clean_email))


def accepted_invitation_for_actor(
    meta: Mapping[str, Any],
    actor: Mapping[str, Any] | None,
    *,
    workspace_id: str = "",
) -> dict[str, Any] | None:
    identity = _trusted_identity(actor)
    if not identity:
        return None
    value = _read(workspace_id, meta)
    consumed = _consumed_ids(value)
    for event in _latest_events(value).values():
        if event.get("state") == "accepted" and _identity_key(event.get("accepted_identity")) == _identity_key(identity) and event.get("invitation_id") not in consumed:
            return dict(event)
    return None


def consume_accepted_invitation(
    meta: dict[str, Any],
    workspace_id: str,
    actor: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    identity = _trusted_identity(actor)
    if not identity:
        return None

    def mutate(value: dict[str, Any]) -> dict[str, Any] | None:
        consumed = _consumed_ids(value)
        for event in _latest_events(value).values():
            invitation_id = str(event.get("invitation_id") or "")
            if event.get("state") != "accepted" or _identity_key(event.get("accepted_identity")) != _identity_key(identity) or invitation_id in consumed:
                continue
            role = _effective_role(value, invitation_id, event.get("role"))
            _events(value).append(
                {
                    "event_id": uuid.uuid4().hex,
                    "event_type": "activation",
                    "invitation_id": invitation_id,
                    "state": "accepted",
                    "occurred_at": _now(),
                    "accepted_identity": identity,
                    "email": event.get("email"),
                    "role": role,
                }
            )
            return {**event, "role": role}
        return None

    return _mutate(workspace_id, meta, mutate)


def effective_invitation_state(meta: Mapping[str, Any], invitation_id: str) -> str | None:
    event = _latest_events(meta).get(_clean(invitation_id))
    return str(event.get("state")) if event else None


def accepted_invitation_identity(
    meta: Mapping[str, Any], workspace_id: str, invitation_id: str
) -> dict[str, str]:
    """Return an accepted identity only after canonical journal validation."""
    value = _read(workspace_id, meta)
    event = _latest_events(value).get(_clean(invitation_id))
    if event is None or event.get("state") != "accepted":
        return {}
    return _identity_fields(event.get("accepted_identity"))


def list_invitation_history(
    meta: Mapping[str, Any],
    workspace_id: str,
    *,
    pseudonym_salt: str | None = None,
) -> list[dict[str, Any]]:
    value = _read(workspace_id, meta)
    events = value.get("workspace_invitation_events") or []
    _validate_events(events)
    salt = member_pseudonym_salt(pseudonym_salt)

    records: dict[str, dict[str, Any]] = {}
    known_identity_by_email: dict[str, dict[str, Any]] = {}
    for raw in events:
        if not isinstance(raw, Mapping):
            continue
        invitation_id = _clean(raw.get("invitation_id"))
        if not invitation_id:
            continue
        kind = str(raw.get("event_type") or "state")
        record = records.setdefault(
            invitation_id,
            {
                "email": "",
                "role": "viewer",
                "invitation_state": "pending",
                "activated": False,
                "accepted_identity": {},
                "updated_at": None,
            },
        )
        if kind == "state":
            record["email"] = _email(raw.get("email"))
            record["role"] = _role(raw.get("role"))
            record["invitation_state"] = _clean(raw.get("state")).lower()
            if _has_identity(raw.get("accepted_identity")):
                record["accepted_identity"] = _identity_fields(raw.get("accepted_identity"))
        elif kind == "role_change":
            record["role"] = _role(raw.get("role"))
        elif kind == "activation":
            record["activated"] = True
            if _has_identity(raw.get("accepted_identity")):
                record["accepted_identity"] = _identity_fields(raw.get("accepted_identity"))
        occurred_at = _clean(raw.get("occurred_at"))
        if occurred_at:
            record["updated_at"] = occurred_at

    for record in records.values():
        email = str(record.get("email") or "")
        identity = record.get("accepted_identity")
        if email and _has_identity(identity):
            known_identity_by_email[email] = _identity_fields(identity)
    for member in meta.get("workspace_members") or []:
        if not isinstance(member, Mapping):
            continue
        email = _clean(member.get("email")).lower()
        if email and "@" in email and _has_identity(member):
            known_identity_by_email[email] = _identity_fields(member)

    history: list[dict[str, Any]] = []
    for invitation_id, record in records.items():
        invitation_state = str(record["invitation_state"])
        effective_state = "removed" if invitation_state == "revoked" and record["activated"] else invitation_state
        subject_identity = record.get("accepted_identity") or known_identity_by_email.get(str(record["email"])) or str(record["email"])
        history.append(
            {
                "invitation_ref": invitation_reference(workspace_id, invitation_id, pseudonym_salt=salt),
                "subject_label": member_subject_label(
                    workspace_id,
                    subject_identity,
                    pseudonym_salt=salt,
                ),
                "role": str(record["role"]),
                "state": effective_state,
                "invitation_state": invitation_state,
                "updated_at": record["updated_at"],
            }
        )
    return sorted(history, key=lambda item: (str(item.get("updated_at") or ""), str(item["invitation_ref"])), reverse=True)


def canonical_member_identity_key(identity: Mapping[str, Any] | str) -> str:
    if isinstance(identity, Mapping):
        actor_id = _clean(identity.get("actor_id") or identity.get("id") or identity.get("oid")).lower()
        tenant_id = _clean(identity.get("tenant_id") or identity.get("tid")).lower()
        if tenant_id and actor_id:
            return f"{tenant_id}\0{actor_id}"
        email = _clean(identity.get("email")).lower()
        if email and "@" in email:
            return email
    else:
        value = _clean(identity).lower()
        if value and "@" in value:
            return value
        if "\0" in value:
            tenant_id, actor_id = value.split("\0", 1)
            if tenant_id and actor_id:
                return f"{tenant_id}\0{actor_id}"
    raise InvitationPersistenceError("member identity is unavailable for safe projection")


def invitation_reference(
    workspace_id: str,
    invitation_id: str,
    *,
    pseudonym_salt: str | None = None,
) -> str:
    return _history_pseudonym(
        "invite",
        workspace_id,
        _clean(invitation_id),
        member_pseudonym_salt(pseudonym_salt),
    )


def member_subject_label(
    workspace_id: str,
    identity: Mapping[str, Any] | str,
    *,
    pseudonym_salt: str | None = None,
) -> str:
    identity_key = canonical_member_identity_key(identity)
    return _history_pseudonym("member", workspace_id, identity_key, member_pseudonym_salt(pseudonym_salt))


def member_pseudonym_salt(value: str | None = None) -> str:
    salt = str(
        value
        or os.environ.get("DF_MEMBER_PSEUDONYM_SALT")
        or os.environ.get("DF_INVITATION_PSEUDONYM_SALT")
        or os.environ.get("DF_ROI_PSEUDONYM_SALT")
        or os.environ.get("DF_WEB_PROXY_SECRET")
        or ""
    ).strip()
    if not salt:
        raise InvitationPersistenceError("member pseudonym salt is not configured")
    return salt


def _history_pseudonym(prefix: str, workspace_id: str, value: str, salt: str) -> str:
    digest = hmac.new(
        salt.encode("utf-8"),
        f"{workspace_id}\0{prefix}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:40]
    return f"{prefix}_{digest}"


def _transition(
    meta: dict[str, Any],
    invitation_id: str,
    target_state: str,
    *,
    identity: Mapping[str, Any] | None,
    provider: Mapping[str, Any] | None,
) -> dict[str, Any]:
    latest = _latest_events(meta).get(invitation_id)
    if latest is None:
        raise InvitationTransitionError("invitation not found")
    if target_state == "accepted" and not identity:
        raise InvitationTransitionError("accepted invitation requires trusted oid and tenant id")
    if latest["state"] == target_state:
        if target_state != "accepted" or _identity_key(latest.get("accepted_identity")) == _identity_key(identity):
            return dict(latest)
        raise InvitationTransitionError("accepted invitation identity cannot change")
    if target_state not in _LEGAL_TRANSITIONS.get(str(latest["state"]), set()):
        raise InvitationTransitionError(f"cannot transition invitation from {latest['state']} to {target_state}")
    event = _state_event(
        invitation_id,
        target_state,
        str(latest.get("email") or ""),
        _effective_role(meta, invitation_id, latest.get("role")),
        latest.get("invited_by"),
        accepted_identity=identity,
        provider=provider or latest.get("provider"),
    )
    _events(meta).append(event)
    return dict(event)


def _revoke_effective(meta: dict[str, Any], email: str) -> list[dict[str, Any]]:
    revoked: list[dict[str, Any]] = []
    latest = _latest_events(meta)
    identities = {_identity_key(event.get("accepted_identity")) for event in latest.values() if event.get("email") == email and isinstance(event.get("accepted_identity"), Mapping)}
    for invitation_id, event in list(latest.items()):
        identity = _identity_key(event.get("accepted_identity"))
        if (event.get("email") != email and identity not in identities) or event.get("state") not in {"pending", "accepted"}:
            continue
        revoked.append(_transition(meta, invitation_id, "revoked", identity=None, provider=event.get("provider")))
    return revoked


def _mutate(workspace_id: str, meta: dict[str, Any], mutation: Callable[[dict[str, Any]], T]) -> T:
    key = workspace_id or "local"
    with workspace_invitation_lock(key):
        if not blob_configured():
            _local_events(meta)
            return mutation(meta)
        for _attempt in range(_MAX_CAS_RETRIES):
            snapshot = download_blob_json(_blob_name(workspace_id))
            if snapshot is not None and (not isinstance(snapshot, dict) or not isinstance(snapshot.get("revision"), int) or not isinstance(snapshot.get("events"), list)):
                raise InvitationPersistenceError("invitation event journal schema is invalid")
            revision = int(snapshot.get("revision") or 0) if isinstance(snapshot, dict) else 0
            events = snapshot.get("events") if isinstance(snapshot, dict) else meta.get("workspace_invitation_events", [])
            if not isinstance(events, list):
                raise InvitationPersistenceError("invitation event journal schema is invalid")
            _validate_events(events)
            draft = {"workspace_invitation_events": copy.deepcopy(events if isinstance(events, list) else [])}
            before = copy.deepcopy(draft["workspace_invitation_events"])
            result = mutation(draft)
            if draft["workspace_invitation_events"] == before:
                meta["workspace_invitation_events"] = before
                return result
            changes = {"revision": revision + 1, "events": draft["workspace_invitation_events"]}
            committed = compare_and_swap_blob_json(_blob_name(workspace_id), expected_revision=revision, changes=changes)
            if committed is not None:
                meta["workspace_invitation_events"] = copy.deepcopy(draft["workspace_invitation_events"])
                return result
        raise InvitationPersistenceError("invitation event persistence conflict")


def _read(workspace_id: str, meta: Mapping[str, Any]) -> dict[str, Any]:
    if not blob_configured():
        return {"workspace_invitation_events": copy.deepcopy(_local_events(meta))}
    snapshot = download_blob_json(_blob_name(workspace_id))
    if snapshot is None and "workspace_invitation_events" not in meta:
        return {"workspace_invitation_events": []}
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("revision"), int) or not isinstance(snapshot.get("events"), list):
        raise InvitationPersistenceError("invitation event journal is unavailable")
    events = snapshot.get("events")
    _validate_events(events)
    return {"workspace_invitation_events": copy.deepcopy(events)}


def _validate_events(events: Any) -> None:
    if not isinstance(events, list):
        raise InvitationPersistenceError("invitation event journal schema is invalid")
    latest: dict[str, dict[str, Any]] = {}
    original_emails: dict[str, str] = {}
    effective_roles: dict[str, str] = {}
    activated: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping):
            raise InvitationPersistenceError("invitation event journal schema is invalid")
        kind = str(event.get("event_type") or "state")
        if kind not in {"state", "activation", "role_change"}:
            raise InvitationPersistenceError("invitation event journal schema is invalid")
        if not _clean(event.get("invitation_id")):
            raise InvitationPersistenceError("invitation event journal schema is invalid")
        invitation_id = _clean(event.get("invitation_id"))
        if kind == "state":
            state = _clean(event.get("state")).lower()
            if state not in INVITATION_STATES:
                raise InvitationPersistenceError("invitation event journal schema is invalid")
            try:
                _email(event.get("email"))
                _role(event.get("role"))
            except InvitationTransitionError as exc:
                raise InvitationPersistenceError("invitation event journal schema is invalid") from exc
            previous = latest.get(invitation_id)
            if previous is None:
                if state != "pending":
                    raise InvitationPersistenceError("invitation event journal sequence is invalid")
                original_emails[invitation_id] = _email(event.get("email"))
                effective_roles[invitation_id] = _role(event.get("role"))
            elif state not in _LEGAL_TRANSITIONS.get(str(previous["state"]), set()):
                raise InvitationPersistenceError("invitation event journal sequence is invalid")
            elif _email(event.get("email")) != original_emails[invitation_id] or _role(event.get("role")) != effective_roles[invitation_id]:
                raise InvitationPersistenceError("invitation event journal sequence is invalid")
            if state == "accepted" and not _has_identity(event.get("accepted_identity")):
                raise InvitationPersistenceError("invitation event journal schema is invalid")
            latest[invitation_id] = dict(event)
        elif kind == "activation":
            try:
                _email(event.get("email"))
                _role(event.get("role"))
            except InvitationTransitionError as exc:
                raise InvitationPersistenceError("invitation event journal schema is invalid") from exc
            accepted = latest.get(invitation_id)
            if (
                invitation_id in activated
                or accepted is None
                or accepted.get("state") != "accepted"
                or not _has_identity(event.get("accepted_identity"))
                or _identity_key(event.get("accepted_identity")) != _identity_key(accepted.get("accepted_identity"))
                or _email(event.get("email")) != original_emails[invitation_id]
                or _role(event.get("role")) != effective_roles[invitation_id]
            ):
                raise InvitationPersistenceError("invitation event journal sequence is invalid")
            activated.add(invitation_id)
        else:
            try:
                _email(event.get("email"))
                _role(event.get("role"))
            except InvitationTransitionError as exc:
                raise InvitationPersistenceError("invitation event journal schema is invalid") from exc
            current = latest.get(invitation_id)
            if (
                current is None
                or current.get("state") not in {"pending", "accepted"}
                or _email(event.get("email")) != original_emails[invitation_id]
            ):
                raise InvitationPersistenceError("invitation event journal sequence is invalid")
            effective_roles[invitation_id] = _role(event.get("role"))


def _events(meta: dict[str, Any]) -> list[dict[str, Any]]:
    if "workspace_invitation_events" not in meta:
        events = []
        meta["workspace_invitation_events"] = events
        return events
    events = meta["workspace_invitation_events"]
    if not isinstance(events, list):
        raise InvitationPersistenceError("invitation event journal schema is invalid")
    return events


def _local_events(meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    if "workspace_invitation_events" not in meta:
        return []
    events = meta["workspace_invitation_events"]
    if not isinstance(events, list):
        raise InvitationPersistenceError("invitation event journal schema is invalid")
    _validate_events(events)
    return events


def _latest_events(meta: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    events = _local_events(meta)
    latest: dict[str, dict[str, Any]] = {}
    for raw in events:
        if not isinstance(raw, Mapping) or str(raw.get("event_type") or "state") != "state":
            continue
        invitation_id = _clean(raw.get("invitation_id"))
        state = _clean(raw.get("state")).lower()
        if invitation_id and state in INVITATION_STATES:
            latest[invitation_id] = dict(raw)
    return latest


def _consumed_ids(meta: Mapping[str, Any]) -> set[str]:
    return {
        _clean(event.get("invitation_id"))
        for event in meta.get("workspace_invitation_events") or []
        if isinstance(event, Mapping) and str(event.get("event_type") or "") == "activation"
    }


def _state_event(
    invitation_id: str,
    state: str,
    email: str,
    role: str,
    invited_by: Mapping[str, Any] | None,
    *,
    accepted_identity: Mapping[str, Any] | None = None,
    provider: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "event_id": uuid.uuid4().hex,
        "event_type": "state",
        "invitation_id": invitation_id,
        "state": state,
        "occurred_at": _now(),
        "email": _email(email),
        "role": _role(role),
        "invited_by": public_actor(dict(invited_by or {})),
    }
    if accepted_identity:
        event["accepted_identity"] = dict(accepted_identity)
    clean_provider = _provider(provider)
    if clean_provider:
        event["provider"] = clean_provider
    return event


def _trusted_identity(actor: Mapping[str, Any] | None) -> dict[str, str]:
    if not is_trusted_tenant_identity(actor):
        return {}
    clean = public_actor(dict(actor or {}))
    return _identity_fields(clean)


def _provider_identity(provider: Mapping[str, Any] | None, inviter: Mapping[str, Any] | None) -> dict[str, str]:
    clean = _provider(provider)
    trusted_inviter = _trusted_identity(inviter)
    if clean.get("source") != "microsoft_graph" or not clean.get("invited_user_id") or not trusted_inviter:
        return {}
    resource_tenant = str(clean.get("resource_tenant_id") or "").lower()
    if clean.get("token_source") == "app_only" and not resource_tenant:
        return {}
    if resource_tenant and resource_tenant != trusted_inviter["tenant_id"]:
        return {}
    return {"actor_id": str(clean["invited_user_id"]).lower(), "tenant_id": str(trusted_inviter["tenant_id"]).lower()}


def _identity_fields(identity: Mapping[str, Any] | None) -> dict[str, str]:
    source = dict(identity or {}) if isinstance(identity, Mapping) else {}
    actor_id = _clean(source.get("actor_id")).lower()
    tenant_id = _clean(source.get("tenant_id")).lower()
    return {"actor_id": actor_id, "tenant_id": tenant_id} if actor_id and tenant_id else {}


def _identity_key(identity: Mapping[str, Any] | None) -> tuple[str, str]:
    fields = _identity_fields(identity)
    return (fields.get("actor_id", ""), fields.get("tenant_id", ""))


def _has_identity(identity: Mapping[str, Any] | None) -> bool:
    actor_id, tenant_id = _identity_key(identity)
    return bool(actor_id and tenant_id)


def _provider(provider: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(provider or {}) if isinstance(provider, Mapping) else {}
    allowed = ("source", "invitation_id", "invited_user_id", "resource_tenant_id", "token_source", "status", "error_code", "status_code")
    clean: dict[str, Any] = {}
    for key in allowed:
        value = source.get(key)
        if key == "status_code" and isinstance(value, int):
            clean[key] = value
        elif isinstance(value, (str, int)) and _clean(value):
            clean[key] = _clean(value)
    return clean


def _state(value: Any) -> str:
    state = _clean(value).lower()
    if state not in INVITATION_STATES or state == "pending":
        raise InvitationTransitionError("invalid invitation state")
    return state


def _role(value: Any) -> str:
    role = _clean(value).lower()
    if role not in INVITATION_ROLES:
        raise InvitationTransitionError("invitation role must be admin, editor, or viewer")
    return role


def _effective_role(meta: Mapping[str, Any], invitation_id: str, fallback: Any) -> str:
    role = fallback
    for event in meta.get("workspace_invitation_events") or []:
        if isinstance(event, Mapping) and event.get("event_type") == "role_change" and str(event.get("invitation_id") or "") == invitation_id:
            role = event.get("role")
    return _role(role)


def _email(value: Any) -> str:
    email = _clean(value).lower()
    if not email or "@" not in email:
        raise InvitationTransitionError("invitation email is required")
    return email


def _blob_name(workspace_id: str) -> str:
    return f"workspaces/{_clean(workspace_id)}/invitation-events.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()[:256]


__all__ = [
    "INVITATION_ROLES",
    "INVITATION_STATES",
    "InvitationPersistenceError",
    "InvitationTransitionError",
    "canonical_member_identity_key",
    "accept_provider_invitation",
    "accepted_invitation_for_actor",
    "consume_accepted_invitation",
    "current_invited_member_role",
    "create_pending_invitation",
    "effective_invitation_state",
    "list_invitation_history",
    "member_pseudonym_salt",
    "member_subject_label",
    "revoke_effective_invitations",
    "transition_invitation",
    "update_invited_member_role",
    "workspace_invitation_lock",
]
