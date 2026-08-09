from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

try:
    from .identity import actor_from_request, is_trusted_tenant_identity, public_actor
except ImportError:
    from identity import actor_from_request, is_trusted_tenant_identity, public_actor


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/session")
def auth_session(request: Request) -> dict[str, Any]:
    """Return the minimal trusted identity required by the portal shell."""
    actor = actor_from_request(request, fallback=False, resolve_groups=False)
    if not is_trusted_tenant_identity(actor):
        return {
            "authenticated": False,
            "identity_provider": "microsoft_entra",
            "identity_source": "unavailable",
        }
    safe_actor = public_actor(actor)
    return {
        "authenticated": True,
        "name": str(safe_actor.get("name") or "DataForge User"),
        "email": str(safe_actor.get("email") or ""),
        "identity_provider": "microsoft_entra",
        "identity_source": "trusted_proxy",
    }
