from __future__ import annotations

import os
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Request

from ..audit_store import record_audit_event
from ..control_plane import workspace_finops_member_identities
from ..identity import actor_from_request, is_trusted_tenant_identity
from ..lineage_sql import build_lineage_sql_connection_factory
from ..workspace_authz import active_workspace_role
from ..workspace_store import list_workspaces
from .member_budget_repository import InMemoryMemberBudgetRepository, MemberBudgetConflictError, MemberBudgetRepository
from .member_budget_service import MemberBudgetService
from .member_directory import MemberDirectory
from .normalization import opaque_ref
from .sql_member_budgets import SqlMemberBudgetRepository
from .sql_repository import FinOpsPersistenceError

router = APIRouter(prefix="/api/finops", tags=["finops-member-budgets"])
_service: MemberBudgetService | None = None


def _enabled(name: str = "DF_FINOPS_MEMBER_BUDGETS_ENABLED") -> bool:
    return str(os.environ.get(name) or "0").strip().lower() in {"1", "true", "yes", "on"}


def _context(request: Request) -> tuple[str, str, tuple[str, ...], Mapping[str, Any]]:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Not found")
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(status_code=403, detail="Trusted tenant identity required")
    roles: dict[str, str] = {}
    for item in list_workspaces():
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("workspace_id") or "").strip()
        if not workspace_id:
            continue
        try:
            role = active_workspace_role(workspace_id, actor)
        except FileNotFoundError:
            continue
        if role:
            roles[workspace_id] = role
    if not roles or any(role not in {"owner", "admin"} for role in roles.values()):
        raise HTTPException(status_code=403, detail="Member budgets require admin or owner")
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    tenant_id, actor_id = str(actor.get("tenant_id") or "").strip(), str(actor.get("actor_id") or "").strip()
    if not secret or not tenant_id or not actor_id:
        raise HTTPException(status_code=503, detail="FinOps scope is unavailable")
    return (
        opaque_ref("tenant", tenant_id, secret=secret),
        opaque_ref("actor", tenant_id, actor_id, secret=secret),
        tuple(sorted(roles)),
        actor,
    )


class _EmptyMemberCostReader:
    def summarize_month(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}


def get_member_budget_service() -> MemberBudgetService:
    global _service
    if _service is None:
        secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
        if not secret:
            raise RuntimeError("FinOps HMAC is unavailable")
        directory = MemberDirectory(identity_loader=workspace_finops_member_identities, hmac_secret=secret)
        if _enabled("DF_FINOPS_SQL_ENABLED"):
            repository: MemberBudgetRepository = SqlMemberBudgetRepository(connection_factory=build_lineage_sql_connection_factory())
            costs: Any = repository
        else:
            repository = InMemoryMemberBudgetRepository()
            costs = _EmptyMemberCostReader()
        _service = MemberBudgetService(repository, directory, costs)
    return _service


def _audit_required(request: Request, workspace_id: str, resource_id: str) -> None:
    try:
        record_audit_event(
            actor_from_request(request, fallback=False),
            "member.manage",
            {"workspace_id": workspace_id, "resource_type": "member", "resource_id": resource_id[:199] or "pending"},
            result="allowed",
            reason_code="authorized",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Audit persistence is required") from exc


def _active_admins(identity_tenant_id: str, workspace_ids: tuple[str, ...]) -> dict[str, str]:
    members = MemberDirectory(identity_loader=workspace_finops_member_identities, hmac_secret=str(os.environ["DF_FINOPS_HMAC_SECRET"])).list_members(identity_tenant_id, workspace_ids)
    return {member.member_ref: member.email for member in members if member.identity_state == "active" and member.role in {"owner", "admin"} and member.email}


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="Not found")
    if isinstance(exc, MemberBudgetConflictError):
        return HTTPException(status_code=409, detail="revision conflict")
    if isinstance(exc, FinOpsPersistenceError):
        return HTTPException(status_code=503, detail="Budget persistence is unavailable")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="Budget persistence is unavailable")


def _allowed_payload(body: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - allowed:
        raise HTTPException(status_code=422, detail="unsupported request fields")
    return {key: body[key] for key in allowed if key in body}


@router.get("/member-budgets")
async def list_member_budgets(request: Request, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
    tenant_ref, _actor_ref, workspace_ids, actor = _context(request)
    try:
        return get_member_budget_service().list_budgets(tenant_ref=tenant_ref, workspace_ids=workspace_ids, cursor=cursor, limit=min(max(limit, 1), 100), identity_tenant_id=str(actor["tenant_id"]))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/member-budgets")
async def create_member_budget(body: dict[str, Any], request: Request) -> Any:
    tenant_ref, actor_ref, workspace_ids, actor = _context(request)
    payload = _allowed_payload(body, {"member_ref", "amount_usd", "thresholds_pct", "enabled", "base_revision"})
    _audit_required(request, workspace_ids[0], "member-budget-create")
    try:
        return get_member_budget_service().save_budget(tenant_ref=tenant_ref, actor_ref=actor_ref, payload=payload)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.patch("/member-budgets/{budget_id}")
async def update_member_budget(budget_id: str, body: dict[str, Any], request: Request) -> Any:
    tenant_ref, actor_ref, workspace_ids, _actor = _context(request)
    payload = _allowed_payload(body, {"member_ref", "amount_usd", "thresholds_pct", "enabled", "base_revision"})
    _audit_required(request, workspace_ids[0], budget_id)
    try:
        return get_member_budget_service().save_budget(tenant_ref=tenant_ref, actor_ref=actor_ref, payload=payload, budget_id=budget_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/member-budgets/{budget_id}/disable")
async def disable_member_budget(budget_id: str, body: dict[str, Any], request: Request) -> Any:
    tenant_ref, actor_ref, workspace_ids, _actor = _context(request)
    if set(body) != {"base_revision"} or isinstance(body.get("base_revision"), bool):
        raise HTTPException(status_code=422, detail="base_revision is required")
    _audit_required(request, workspace_ids[0], budget_id)
    try:
        return get_member_budget_service().disable_budget(tenant_ref=tenant_ref, actor_ref=actor_ref, budget_id=budget_id, base_revision=int(body["base_revision"]))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/notification-settings")
async def get_notification_settings(request: Request) -> Any:
    tenant_ref, _actor_ref, _workspace_ids, _actor = _context(request)
    try:
        value = get_member_budget_service().get_notification(tenant_ref=tenant_ref)
    except Exception as exc:
        raise _map_error(exc) from exc
    if value is None:
        raise HTTPException(status_code=404, detail="Not found")
    return value


@router.put("/notification-settings")
async def put_notification_settings(body: dict[str, Any], request: Request) -> Any:
    tenant_ref, actor_ref, workspace_ids, actor = _context(request)
    payload = _allowed_payload(body, {"recipient_actor_ref", "sender_display_name", "subject_template", "body_template", "enabled", "base_revision"})
    _audit_required(request, workspace_ids[0], "member-budget-notification")
    try:
        return get_member_budget_service().save_notification(tenant_ref=tenant_ref, actor_ref=actor_ref, payload=payload, active_admins=_active_admins(str(actor["tenant_id"]), workspace_ids))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/budget-alerts")
async def list_budget_alerts(request: Request, budget_id: str | None = None) -> dict[str, Any]:
    tenant_ref, _actor_ref, _workspace_ids, _actor = _context(request)
    try:
        return get_member_budget_service().list_alerts(tenant_ref=tenant_ref, budget_id=budget_id)
    except Exception as exc:
        raise _map_error(exc) from exc
