from __future__ import annotations

"""Server-owned, bounded member-budget sweep; no user scope is accepted."""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class ServerDirectory:
    scopes: dict[str, tuple[str, ...]]
    members: dict[str, set[str]]
    admins: dict[str, dict[str, str]]
    names: dict[str, dict[str, str]]


def run_sweep(
    evaluator: Any,
    scopes: dict[str, tuple[str, ...]],
    *,
    now_factory: Callable[[], datetime] | None = None,
) -> int:
    """Continue across tenants, but report any infrastructure evaluation failure."""
    clock = now_factory or (lambda: datetime.now(timezone.utc))
    infrastructure_failed = False
    for tenant_ref, workspace_ids in scopes.items():
        try:
            # Email delivery failures are represented in EvaluationSummary and
            # remain isolated. Raised errors are database/directory/runtime failures.
            evaluator.evaluate_tenant(
                tenant_ref,
                now=clock(),
                workspace_ids=workspace_ids,
            )
        except Exception:
            infrastructure_failed = True
            continue
    return 1 if infrastructure_failed else 0


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _list_workspaces() -> list[dict[str, Any]]:
    from ..workspace_store import list_workspaces

    return list_workspaces()


def _workspace_identities(workspace_id: str) -> list[dict[str, str]]:
    from ..control_plane import workspace_finops_member_identities

    return workspace_finops_member_identities(workspace_id)


def _server_directory(*, secret: str) -> ServerDirectory:
    from .normalization import opaque_ref

    workspace_ids = sorted(
        {
            str(item.get("workspace_id") or "").strip()
            for item in _list_workspaces()
            if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
        }
    )
    if not workspace_ids or len(workspace_ids) > 100:
        raise RuntimeError("workspace tenant mapping unavailable")

    scopes: dict[str, list[str]] = {}
    members: dict[str, set[str]] = {}
    admins: dict[str, dict[str, str]] = {}
    names: dict[str, dict[str, str]] = {}

    for workspace_id in workspace_ids:
        identities = _workspace_identities(workspace_id)
        raw_tenants = {
            str(item.get("tenant_id") or "").strip()
            for item in identities
            if isinstance(item, dict)
            and str(item.get("tenant_id") or "").strip()
        }
        if len(raw_tenants) != 1:
            raise RuntimeError("workspace tenant mapping unavailable")
        raw_tenant = next(iter(raw_tenants))
        if any(
            not isinstance(item, dict)
            or str(item.get("tenant_id") or "").strip() != raw_tenant
            or not str(item.get("actor_id") or "").strip()
            for item in identities
        ):
            raise RuntimeError("workspace tenant mapping unavailable")

        tenant_ref = opaque_ref("tenant", raw_tenant, secret=secret)
        scopes.setdefault(tenant_ref, []).append(workspace_id)
        members.setdefault(tenant_ref, set())
        admins.setdefault(tenant_ref, {})
        names.setdefault(tenant_ref, {})

        for item in identities:
            if str(item.get("status") or "").strip().lower() != "active":
                continue
            raw_actor = str(item.get("actor_id") or "").strip()
            actor_ref = opaque_ref(
                "actor", raw_tenant, raw_actor, secret=secret
            )
            members[tenant_ref].add(actor_ref)
            display_name = str(item.get("name") or "").strip()
            if display_name:
                names[tenant_ref].setdefault(actor_ref, display_name)
            role = str(item.get("role") or "").strip().lower()
            email = str(item.get("email") or "").strip()
            if role in {"owner", "admin"} and email:
                existing = admins[tenant_ref].get(actor_ref)
                if existing and existing.casefold() != email.casefold():
                    raise RuntimeError("workspace tenant mapping unavailable")
                admins[tenant_ref][actor_ref] = email

    return ServerDirectory(
        scopes={
            tenant_ref: tuple(sorted(set(values)))
            for tenant_ref, values in scopes.items()
        },
        members=members,
        admins=admins,
        names=names,
    )


def _build_runtime() -> tuple[Any, Any, ServerDirectory]:
    from ..lineage_sql import build_lineage_sql_connection_factory
    from .acs_email import acs_email_sender_from_environment
    from .member_budget_evaluator import MemberBudgetEvaluator
    from .sql_member_budgets import SqlMemberBudgetRepository

    secret = str(os.getenv("DF_FINOPS_HMAC_SECRET") or "").strip()
    portal_url = str(os.getenv("DF_FINOPS_PORTAL_URL") or "").strip()
    if not secret or not portal_url:
        raise RuntimeError("budget refresh configuration unavailable")

    repository = SqlMemberBudgetRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )
    directory = _server_directory(secret=secret)
    evaluator = MemberBudgetEvaluator(
        repository=repository,
        costs=repository,
        active_member_refs=lambda tenant_ref, _workspace_ids: set(
            directory.members.get(tenant_ref, set())
        ),
        active_admins=lambda tenant_ref, _workspace_ids: dict(
            directory.admins.get(tenant_ref, {})
        ),
        member_names=lambda tenant_ref, _workspace_ids: dict(
            directory.names.get(tenant_ref, {})
        ),
        sender=acs_email_sender_from_environment(),
        portal_url=portal_url,
    )
    return repository, evaluator, directory


def main() -> int:
    # Automatic delivery is an independent production gate and defaults off.
    # Do not enumerate workspaces, construct credentials, or touch SQL while off.
    if not _enabled("DF_FINOPS_EMAIL_ALERTS_ENABLED"):
        return 0
    try:
        repository, evaluator, directory = _build_runtime()
        enabled_tenants = tuple(repository.list_enabled_tenants())
        if len(enabled_tenants) > 1000 or any(
            tenant_ref not in directory.scopes for tenant_ref in enabled_tenants
        ):
            return 1
        scopes = {
            tenant_ref: directory.scopes[tenant_ref]
            for tenant_ref in sorted(set(enabled_tenants))
        }
        return run_sweep(evaluator, scopes)
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ServerDirectory",
    "main",
    "run_sweep",
]
