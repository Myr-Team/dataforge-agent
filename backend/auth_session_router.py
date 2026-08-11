from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Any

from fastapi import APIRouter, Request, Response

try:
    from .identity import actor_from_request, is_trusted_tenant_identity, public_actor
except ImportError:
    from identity import actor_from_request, is_trusted_tenant_identity, public_actor


router = APIRouter(prefix="/api/auth", tags=["auth"])
_SESSION_COOKIE = "df_settings_session"
_PROCESS_SESSION_SECRET = secrets.token_bytes(32)


def _session_secret() -> bytes:
    """Use the proxy secret when configured, with a process-local secure fallback."""
    return (os.environ.get("DF_WEB_PROXY_SECRET") or "").encode("utf-8") or _PROCESS_SESSION_SECRET


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _opaque_ref(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{_b64(hmac.new(_session_secret(), payload, hashlib.sha256).digest())}"


def _session_cookie(actor_id: str, tenant_id: str, supplied: str) -> tuple[str, str, bool]:
    """Return a browser-session nonce that is signed and bound to this actor/tenant."""
    nonce, separator, signature = supplied.partition(".")
    expected = _b64(hmac.new(
        _session_secret(), f"session\0{tenant_id}\0{actor_id}\0{nonce}".encode("utf-8"), hashlib.sha256
    ).digest()) if nonce and separator else ""
    if expected and hmac.compare_digest(signature, expected):
        return nonce, supplied, False
    nonce = _b64(secrets.token_bytes(32))
    signature = _b64(hmac.new(
        _session_secret(), f"session\0{tenant_id}\0{actor_id}\0{nonce}".encode("utf-8"), hashlib.sha256
    ).digest())
    return nonce, f"{nonce}.{signature}", True


@router.get("/session")
def auth_session(request: Request, response: Response) -> dict[str, Any]:
    """Return the minimal trusted identity required by the portal shell."""
    actor = actor_from_request(request, fallback=False, resolve_groups=False)
    if not is_trusted_tenant_identity(actor):
        response.delete_cookie(_SESSION_COOKIE, httponly=True, samesite="lax", path="/")
        return {
            "authenticated": False,
            "identity_provider": "microsoft_entra",
            "identity_source": "unavailable",
        }
    safe_actor = public_actor(actor)
    actor_id = str(actor.get("actor_id") or "").strip()
    tenant_id = str(actor.get("tenant_id") or "").strip()
    session_nonce, signed_session, session_changed = _session_cookie(
        actor_id, tenant_id, str(request.cookies.get(_SESSION_COOKIE) or "")
    )
    if session_changed:
        response.set_cookie(
            _SESSION_COOKIE,
            signed_session,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/",
        )
    return {
        "authenticated": True,
        "name": str(safe_actor.get("name") or "DataForge User"),
        "email": str(safe_actor.get("email") or ""),
        "identity_provider": "microsoft_entra",
        "identity_source": "trusted_proxy",
        "tenant_ref": _opaque_ref("tenant", "tenant", tenant_id),
        "actor_ref": _opaque_ref("actor", "actor", tenant_id, actor_id),
        "session_ref": _opaque_ref("session", "session-ref", tenant_id, actor_id, session_nonce),
    }
