from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Any

from .identity import is_trusted_tenant_identity


@dataclass(frozen=True)
class TenantAdminCapability:
    allowed: bool
    source: str
    role_name: str


def _configured_values(name: str) -> set[str]:
    return {
        value.strip().lower()
        for value in re.split(r"[,;\s]+", str(os.environ.get(name) or ""))
        if value.strip()
    }


def tenant_admin_capability(
    actor: Mapping[str, Any] | None,
    *,
    workspace_roles: Mapping[str, str] | None = None,
    allow_all_workspace_owner: bool = False,
) -> TenantAdminCapability:
    role_name = str(
        os.environ.get("DF_FINOPS_TENANT_ADMIN_ROLE")
        or "DataForge.FinOpsAdmin"
    ).strip()
    if not is_trusted_tenant_identity(actor):
        return TenantAdminCapability(False, "untrusted_identity", role_name)

    normalized_roles = {
        str(value).strip().lower()
        for value in (actor or {}).get("roles", [])
        if str(value).strip()
    }
    if role_name and role_name.lower() in normalized_roles:
        return TenantAdminCapability(True, "entra_app_role", role_name)

    actor_id = str((actor or {}).get("actor_id") or "").strip().lower()
    if actor_id and actor_id in _configured_values("DF_FINOPS_TENANT_OWNER_OIDS"):
        return TenantAdminCapability(True, "configured_tenant_owner", role_name)

    roles = {
        str(workspace_id).strip(): str(role).strip().lower()
        for workspace_id, role in (workspace_roles or {}).items()
        if str(workspace_id).strip() and str(role).strip()
    }
    if allow_all_workspace_owner and roles and all(
        role == "owner" for role in roles.values()
    ):
        return TenantAdminCapability(True, "all_workspaces_owner", role_name)

    return TenantAdminCapability(False, "read_only", role_name)
