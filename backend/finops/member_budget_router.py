from __future__ import annotations

import math
import os
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Request

from ..audit_store import record_audit_event
from ..control_plane import workspace_finops_member_identities
from ..identity import actor_from_request, is_trusted_tenant_identity
from ..lineage_sql import build_lineage_sql_connection_factory
from ..workspace_authz import active_workspace_role
from ..workspace_store import list_workspaces
from .member_budget_repository import MemberBudgetConflictError, MemberBudgetRepository
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
    # This is deliberately the first operation in every handler.  Handlers
    # take only Request so FastAPI cannot expose validation behaviour while the
    # feature is disabled.
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
    return opaque_ref("tenant", tenant_id, secret=secret), opaque_ref("actor", tenant_id, actor_id, secret=secret), tuple(sorted(roles)), actor


def get_member_budget_service() -> MemberBudgetService:
    """Return durable production storage; tests override this dependency explicitly."""
    global _service
    if not _enabled("DF_FINOPS_SQL_ENABLED"):
        raise FinOpsPersistenceError("durable FinOps SQL is required")
    if _service is None:
        secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
        if not secret:
            raise FinOpsPersistenceError("FinOps HMAC is unavailable")
        repository: MemberBudgetRepository = SqlMemberBudgetRepository(connection_factory=build_lineage_sql_connection_factory())
        directory = MemberDirectory(identity_loader=workspace_finops_member_identities, hmac_secret=secret)
        _service = MemberBudgetService(repository, directory, repository)
    return _service


def _audit_required(request: Request, workspace_id: str, resource_id: str) -> None:
    try:
        record_audit_event(actor_from_request(request, fallback=False), "member.manage", {"workspace_id": workspace_id, "resource_type": "member", "resource_id": resource_id[:199] or "pending"}, result="allowed", reason_code="authorized")
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


async def _object_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="JSON object body is required") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="JSON object body is required")
    return body


def _limit(request: Request) -> int:
    value = request.query_params.get("limit")
    if value is None:
        return 50
    if not value.isdecimal() or not 1 <= int(value) <= 100:
        raise HTTPException(status_code=422, detail="limit must be an integer from 1 to 100")
    return int(value)


def _cursor(request: Request) -> str | None:
    value = request.query_params.get("cursor")
    if value is not None and not value.isdecimal():
        raise HTTPException(status_code=422, detail="invalid cursor")
    return value


def _payload(body: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    if set(body) - allowed:
        raise HTTPException(status_code=422, detail="unsupported request fields")
    return {key: body[key] for key in allowed if key in body}


def _strict_revision(value: object) -> None:
    if type(value) is not int or value < 0:
        raise HTTPException(status_code=422, detail="base_revision must be a non-negative integer")


def _strict_budget_payload(payload: dict[str, Any], *, create: bool) -> None:
    if "base_revision" not in payload:
        raise HTTPException(status_code=422, detail="base_revision is required")
    _strict_revision(payload["base_revision"])
    if create and (type(payload.get("member_ref")) is not str or not payload["member_ref"]):
        raise HTTPException(status_code=422, detail="member_ref is required")
    if create and "amount_usd" not in payload:
        raise HTTPException(status_code=422, detail="amount_usd is required")
    if "amount_usd" in payload and (
        type(payload["amount_usd"]) not in {int, float}
        or isinstance(payload["amount_usd"], bool)
        or not math.isfinite(payload["amount_usd"])
        or payload["amount_usd"] <= 0
    ):
        raise HTTPException(status_code=422, detail="amount_usd must be a positive finite number")
    if "enabled" in payload and type(payload["enabled"]) is not bool:
        raise HTTPException(status_code=422, detail="enabled must be a boolean")
    if "thresholds_pct" in payload and (type(payload["thresholds_pct"]) is not list or any(type(item) is not int for item in payload["thresholds_pct"])):
        raise HTTPException(status_code=422, detail="thresholds_pct must be an integer array")


def _strict_notification_payload(payload: dict[str, Any]) -> None:
    if "base_revision" not in payload:
        raise HTTPException(status_code=422, detail="base_revision is required")
    _strict_revision(payload["base_revision"])
    if "enabled" in payload and type(payload["enabled"]) is not bool:
        raise HTTPException(status_code=422, detail="enabled must be a boolean")
    for key in {"recipient_actor_ref", "sender_display_name", "subject_template", "body_template"} & set(payload):
        if type(payload[key]) is not str:
            raise HTTPException(status_code=422, detail=f"{key} must be a string")


def _item_envelope(item: Any, *, data_status: str = "unavailable") -> dict[str, Any]:
    return {"item": item, "freshness": "recorded", "coverage": "request_estimated_cost", "data_status": data_status, "currency": "USD"}


def _list_envelope(value: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
    result = dict(value)
    result.setdefault("items", [])
    result.setdefault("cursor", {"next": None, "limit": limit})
    result.setdefault("freshness", "recorded")
    result.setdefault("coverage", "request_estimated_cost")
    result.setdefault("data_status", "unavailable")
    result.setdefault("currency", "USD")
    return result


@router.get("/member-budgets")
async def list_member_budgets(request: Request) -> dict[str, Any]:
    tenant_ref, _actor_ref, workspace_ids, actor = _context(request)
    limit = _limit(request)
    try:
        return _list_envelope(get_member_budget_service().list_budgets(tenant_ref=tenant_ref, workspace_ids=workspace_ids, cursor=_cursor(request), limit=limit, identity_tenant_id=str(actor["tenant_id"])), limit=limit)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/member-budget-members")
async def list_eligible_member_budgets(request: Request) -> dict[str, Any]:
    tenant_ref, _actor_ref, workspace_ids, actor = _context(request)
    limit = _limit(request)
    try:
        return _list_envelope(get_member_budget_service().list_eligible_members(tenant_ref=tenant_ref, identity_tenant_id=str(actor["tenant_id"]), workspace_ids=workspace_ids, cursor=_cursor(request), limit=limit), limit=limit)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/member-budgets")
async def create_member_budget(request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, workspace_ids, actor = _context(request)
    payload = _payload(await _object_body(request), {"member_ref", "amount_usd", "thresholds_pct", "enabled", "base_revision"})
    _strict_budget_payload(payload, create=True)
    try:
        service = get_member_budget_service()
        if not service.is_eligible_member(member_ref=payload["member_ref"], identity_tenant_id=str(actor["tenant_id"]), workspace_ids=workspace_ids):
            raise KeyError(payload["member_ref"])
        _audit_required(request, workspace_ids[0], "member-budget-create")
        return _item_envelope(service.save_budget(tenant_ref=tenant_ref, actor_ref=actor_ref, payload=payload))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.patch("/member-budgets/{budget_id}")
async def update_member_budget(budget_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, workspace_ids, _actor = _context(request)
    payload = _payload(await _object_body(request), {"member_ref", "amount_usd", "thresholds_pct", "enabled", "base_revision"})
    _strict_budget_payload(payload, create=False)
    _audit_required(request, workspace_ids[0], budget_id)
    try:
        return _item_envelope(get_member_budget_service().save_budget(tenant_ref=tenant_ref, actor_ref=actor_ref, payload=payload, budget_id=budget_id))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/member-budgets/{budget_id}/disable")
async def disable_member_budget(budget_id: str, request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, workspace_ids, _actor = _context(request)
    body = await _object_body(request)
    if set(body) != {"base_revision"}:
        raise HTTPException(status_code=422, detail="base_revision is required")
    _strict_revision(body["base_revision"])
    _audit_required(request, workspace_ids[0], budget_id)
    try:
        return _item_envelope(get_member_budget_service().disable_budget(tenant_ref=tenant_ref, actor_ref=actor_ref, budget_id=budget_id, base_revision=body["base_revision"]))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/notification-settings")
async def get_notification_settings(request: Request) -> dict[str, Any]:
    tenant_ref, _actor_ref, _workspace_ids, _actor = _context(request)
    try:
        value = get_member_budget_service().get_notification(tenant_ref=tenant_ref)
    except Exception as exc:
        raise _map_error(exc) from exc
    if value is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _item_envelope(value)


@router.put("/notification-settings")
async def put_notification_settings(request: Request) -> dict[str, Any]:
    tenant_ref, actor_ref, workspace_ids, actor = _context(request)
    payload = _payload(await _object_body(request), {"recipient_actor_ref", "sender_display_name", "subject_template", "body_template", "enabled", "base_revision"})
    _strict_notification_payload(payload)
    _audit_required(request, workspace_ids[0], "member-budget-notification")
    try:
        return _item_envelope(get_member_budget_service().save_notification(tenant_ref=tenant_ref, actor_ref=actor_ref, payload=payload, active_admins=_active_admins(str(actor["tenant_id"]), workspace_ids)))
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/budget-alerts")
async def list_budget_alerts(request: Request) -> dict[str, Any]:
    tenant_ref, _actor_ref, _workspace_ids, _actor = _context(request)
    budget_id = request.query_params.get("budget_id")
    if budget_id is not None and not budget_id:
        raise HTTPException(status_code=422, detail="budget_id is invalid")
    limit = _limit(request)
    try:
        return _list_envelope(get_member_budget_service().list_alerts(tenant_ref=tenant_ref, budget_id=budget_id, cursor=_cursor(request), limit=limit), limit=limit)
    except Exception as exc:
        raise _map_error(exc) from exc
