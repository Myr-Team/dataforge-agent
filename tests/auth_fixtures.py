from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping

import backend.workspace_authz as workspace_authz


def trusted_headers(
    *,
    actor_id: str = "fixture-owner-oid",
    tenant_id: str = "fixture-tenant",
    email: str = "owner@contoso.com",
    proxy_secret: str = "test-proxy-secret",
) -> dict[str, str]:
    principal = {
        "userDetails": email,
        "claims": [
            {"typ": "preferred_username", "val": email},
            {"typ": "oid", "val": actor_id},
            {"typ": "tid", "val": tenant_id},
        ],
    }
    encoded = base64.urlsafe_b64encode(json.dumps(principal).encode("utf-8")).decode("ascii")
    return {
        "x-ms-client-principal": encoded,
        "x-dataforge-proxy-secret": proxy_secret,
    }


def install_workspace_memberships(
    monkeypatch,
    memberships: Mapping[str, Iterable[Mapping[str, str]]],
    *,
    proxy_secret: str = "test-proxy-secret",
) -> None:
    """Install exact active memberships for requests authenticated by this fixture."""
    durable_meta = {
        workspace_id: {"workspace_members": [dict(member) for member in members]}
        for workspace_id, members in memberships.items()
    }
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", proxy_secret)
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda workspace_id: durable_meta.get(str(workspace_id), {}),
    )


def active_member(actor_id: str, tenant_id: str, role: str) -> dict[str, str]:
    return {
        "actor_id": actor_id,
        "tenant_id": tenant_id,
        "role": role,
        "status": "active",
    }
