from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .member_budgets import MemberCostSummary
from .normalization import opaque_ref


class FinOpsMember(BaseModel):
    """Administrator-only projection of a trusted workspace member.

    Raw Entra identifiers are intentionally converted before this value is
    constructed; this record must never become a general member API payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    member_ref: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=160)
    email: str = Field(default="", max_length=320, repr=False)
    role: Literal["owner", "admin", "editor", "viewer"]
    identity_state: Literal["active", "inactive"]
    workspace_ids: tuple[str, ...]
    department_labels: tuple[str, ...]


class MemberMonthlyCost(BaseModel):
    """A truthful UTC-month cost projection from reconciled request facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_ref: str = Field(min_length=1, max_length=128)
    estimated_spend_usd: Decimal | None
    priced_requests: int = Field(ge=0)
    total_requests: int = Field(ge=0)
    unpriced_requests: int = Field(ge=0)
    pricing_coverage_pct: float | None = Field(default=None, ge=0, le=100)
    currency: Literal["USD"] = "USD"
    primary_model: str | None = Field(default=None, max_length=160)
    data_status: Literal["complete", "partial", "unavailable"]
    freshness: Literal["recorded"] = "recorded"


class _MemberCostRepository(Protocol):
    def summarize_member_costs(
        self,
        *,
        tenant_ref: str,
        from_value: str,
        to_value: str,
        workspace_ids: tuple[str, ...],
    ) -> dict[str, MemberCostSummary]: ...


class MemberDirectory:
    """Resolve only already-authorized trusted workspace identities.

    The loader is deliberately injected so this component cannot issue Graph
    queries or broaden workspace access. `workspace_finops_member_identities`
    is the production loader and has already validated Easy Auth provenance.
    """

    def __init__(
        self,
        *,
        identity_loader: Callable[[str], list[dict[str, str]]],
        hmac_secret: str,
        department_loader: Callable[[str, str], str | None] | None = None,
    ) -> None:
        if not hmac_secret:
            raise ValueError("FinOps HMAC secret is required")
        self._identity_loader = identity_loader
        self._hmac_secret = hmac_secret
        self._department_loader = department_loader or (lambda _tenant_ref, _workspace_id: None)

    def list_members(self, tenant_ref: str, workspace_ids: tuple[str, ...]) -> tuple[FinOpsMember, ...]:
        """Merge authorized workspaces by stable tenant + actor identity."""
        members: dict[tuple[str, str], dict[str, object]] = {}
        for workspace_id in dict.fromkeys(workspace_ids):
            for raw in self._identity_loader(workspace_id):
                tenant_id = _text(raw.get("tenant_id"))
                actor_id = _text(raw.get("actor_id"))
                if not tenant_id or not actor_id or tenant_id != tenant_ref:
                    continue
                key = (tenant_id, actor_id)
                row = members.setdefault(
                    key,
                    {
                        "member_ref": opaque_ref("actor", tenant_id, actor_id, secret=self._hmac_secret),
                        "display_name": _text(raw.get("name")) or "Former member",
                        "email": _text(raw.get("email")),
                        "role": _role(raw.get("role")),
                        "identity_state": _identity_state(raw.get("status")),
                        "workspace_ids": [],
                        "department_labels": [],
                    },
                )
                if _identity_state(raw.get("status")) == "active":
                    row["identity_state"] = "active"
                    row["display_name"] = _text(raw.get("name")) or str(row["display_name"])
                    row["email"] = _text(raw.get("email")) or str(row["email"])
                    row["role"] = _role(raw.get("role"))
                row["workspace_ids"].append(workspace_id)
                department = self._department_loader(tenant_ref, workspace_id)
                if department:
                    row["department_labels"].append(department)
        return tuple(
            FinOpsMember(
                member_ref=str(row["member_ref"]),
                display_name=str(row["display_name"]),
                email=str(row["email"]),
                role=str(row["role"]),
                identity_state=str(row["identity_state"]),
                workspace_ids=tuple(sorted(set(row["workspace_ids"]))),
                department_labels=tuple(sorted(set(row["department_labels"]))),
            )
            for _key, row in sorted(members.items(), key=lambda item: str(item[1]["member_ref"]))
        )


class MemberCostReader:
    """Apply UTC calendar-month bounds to repository-summarized request facts."""

    def __init__(self, repository: _MemberCostRepository) -> None:
        self._repository = repository

    def summarize_month(
        self,
        tenant_ref: str,
        month_start: datetime,
        month_end: datetime,
        workspace_ids: tuple[str, ...],
    ) -> dict[str, MemberMonthlyCost]:
        start = _month_start(month_start)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        if _as_utc(month_start) != start or _as_utc(month_end) != end:
            raise ValueError("month bounds must be one UTC calendar month")
        authorized_workspaces = _bounded_workspace_ids(workspace_ids)
        if not authorized_workspaces:
            return {}
        summaries = self._repository.summarize_member_costs(
            tenant_ref=tenant_ref,
            from_value=_utc_value(start),
            to_value=_utc_value(end),
            workspace_ids=authorized_workspaces,
        )
        return {
            actor_ref: MemberMonthlyCost(
                actor_ref=value.actor_ref,
                estimated_spend_usd=value.estimated_spend_usd,
                priced_requests=value.priced_requests,
                total_requests=value.total_requests,
                unpriced_requests=value.total_requests - value.priced_requests,
                pricing_coverage_pct=value.pricing_coverage_pct,
                primary_model=value.primary_model,
                data_status=value.data_status or "unavailable",
            )
            for actor_ref, value in summaries.items()
        }


def _month_start(value: datetime) -> datetime:
    return _as_utc(value).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("month bounds must be timezone-aware UTC timestamps")
    return value.astimezone(timezone.utc)


def _utc_value(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _text(value: object) -> str:
    return str(value or "").strip()


def _role(value: object) -> Literal["owner", "admin", "editor", "viewer"]:
    role = _text(value).lower()
    return role if role in {"owner", "admin", "editor", "viewer"} else "viewer"


def _identity_state(value: object) -> Literal["active", "inactive"]:
    return "active" if _text(value).lower() == "active" else "inactive"


def _bounded_workspace_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(_text(value) for value in values if _text(value)))
    if len(unique) > 100:
        raise ValueError("authorized workspace scope exceeds limit")
    return unique


__all__ = ["FinOpsMember", "MemberCostReader", "MemberDirectory", "MemberMonthlyCost"]
