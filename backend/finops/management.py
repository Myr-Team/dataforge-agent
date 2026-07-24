from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import TokenUsage


class Department(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    display_name: str = Field(min_length=1, max_length=160)
    cost_center: str | None = Field(default=None, max_length=128)
    status: Literal["active", "archived"] = "active"
    version: int = Field(default=1, ge=1)
    updated_at: str
    updated_by: str


class PriceCardItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment: str = Field(min_length=1, max_length=160)
    input_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float | None = Field(default=None, ge=0)
    cached_input_per_million: float | None = Field(default=None, ge=0)
    reasoning_per_million: float | None = Field(default=None, ge=0)


class PriceCardRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str
    status: Literal["draft", "under_review", "active", "retired"]
    currency: Literal["USD"] = "USD"
    items: list[PriceCardItem]
    created_by: str
    reviewed_by: str | None = None
    created_at: str
    reviewed_at: str | None = None
    activated_at: str | None = None


class ErrorRatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_pct: float = Field(default=5, gt=0, le=100)
    minimum_requests: int = Field(default=20, ge=1)
    window_minutes: int = Field(default=15, ge=1, le=1440)


class P95LatencyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_ms: int = Field(default=2000, ge=1)
    minimum_requests: int = Field(default=20, ge=1)
    window_minutes: int = Field(default=15, ge=1, le=1440)


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_budget_usd: float = Field(gt=0)
    warning_pct: float = Field(default=80, gt=0, le=100)
    critical_pct: float = Field(default=100, ge=100)


class CoveragePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_pct: float = Field(default=95, ge=0, le=100)


class CachePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_hit_rate_pct: float = Field(default=20, ge=0, le=100)
    minimum_requests: int = Field(default=20, ge=1)


class TokenSpikePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    multiplier: float = Field(default=2, gt=1, le=100)
    lookback_days: int = Field(default=7, ge=1, le=30)


class UnpricedRequestsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_pct: float = Field(default=5, ge=0, le=100)


_POLICY_MODELS: dict[str, type[BaseModel]] = {
    "error_rate": ErrorRatePolicy,
    "p95_latency": P95LatencyPolicy,
    "daily_cost_budget": BudgetPolicy,
    "apim_coverage": CoveragePolicy,
    "cache_hit_rate": CachePolicy,
    "token_spike": TokenSpikePolicy,
    "unpriced_requests": UnpricedRequestsPolicy,
}


class FinOpsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    policy_type: str
    status: Literal["enabled", "disabled"] = "enabled"
    configuration: dict[str, Any]
    version: int = 1
    updated_at: str
    updated_by: str


class InMemoryManagementRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self.departments: dict[tuple[str, str], Department] = {}
        self.workspace_departments: dict[tuple[str, str], str | None] = {}
        self.price_cards: dict[tuple[str, str], PriceCardRevision] = {}
        self.policies: dict[tuple[str, str], FinOpsPolicy] = {}

    def get_department(self, tenant_ref: str, department_id: str) -> Department | None:
        with self._lock:
            value = self.departments.get((tenant_ref, department_id))
        return value.model_copy(deep=True) if value else None

    def save_department(self, tenant_ref: str, value: Department) -> Department:
        with self._lock:
            self.departments[(tenant_ref, value.department_id)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def list_departments(self, tenant_ref: str) -> list[Department]:
        with self._lock:
            return [value.model_copy(deep=True) for (tenant, _), value in self.departments.items() if tenant == tenant_ref]

    def get_workspace_department(self, tenant_ref: str, workspace_id: str) -> str | None:
        with self._lock:
            return self.workspace_departments.get((tenant_ref, workspace_id))

    def save_workspace_department(self, tenant_ref: str, workspace_id: str, department_id: str | None) -> None:
        with self._lock:
            self.workspace_departments[(tenant_ref, workspace_id)] = department_id

    def get_price_card(self, tenant_ref: str, revision_id: str) -> PriceCardRevision | None:
        with self._lock:
            value = self.price_cards.get((tenant_ref, revision_id))
        return value.model_copy(deep=True) if value else None

    def save_price_card(self, tenant_ref: str, value: PriceCardRevision) -> PriceCardRevision:
        with self._lock:
            self.price_cards[(tenant_ref, value.revision_id)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def list_price_cards(self, tenant_ref: str) -> list[PriceCardRevision]:
        with self._lock:
            return [value.model_copy(deep=True) for (tenant, _), value in self.price_cards.items() if tenant == tenant_ref]

    def get_policy(self, tenant_ref: str, policy_id: str) -> FinOpsPolicy | None:
        with self._lock:
            value = self.policies.get((tenant_ref, policy_id))
        return value.model_copy(deep=True) if value else None

    def save_policy(self, tenant_ref: str, value: FinOpsPolicy) -> FinOpsPolicy:
        with self._lock:
            self.policies[(tenant_ref, value.policy_id)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def list_policies(self, tenant_ref: str) -> list[FinOpsPolicy]:
        with self._lock:
            return [value.model_copy(deep=True) for (tenant, _), value in self.policies.items() if tenant == tenant_ref]


class ManagementRepository(Protocol):
    def get_department(self, tenant_ref: str, department_id: str) -> Department | None: ...
    def save_department(self, tenant_ref: str, value: Department) -> Department: ...
    def list_departments(self, tenant_ref: str) -> list[Department]: ...
    def get_workspace_department(self, tenant_ref: str, workspace_id: str) -> str | None: ...
    def save_workspace_department(self, tenant_ref: str, workspace_id: str, department_id: str | None) -> None: ...
    def get_price_card(self, tenant_ref: str, revision_id: str) -> PriceCardRevision | None: ...
    def save_price_card(self, tenant_ref: str, value: PriceCardRevision) -> PriceCardRevision: ...
    def list_price_cards(self, tenant_ref: str) -> list[PriceCardRevision]: ...
    def get_policy(self, tenant_ref: str, policy_id: str) -> FinOpsPolicy | None: ...
    def save_policy(self, tenant_ref: str, value: FinOpsPolicy) -> FinOpsPolicy: ...
    def list_policies(self, tenant_ref: str) -> list[FinOpsPolicy]: ...


class FinOpsManagementService:
    def __init__(self, repository: ManagementRepository) -> None:
        self._repository = repository

    def create_department(
        self,
        *,
        tenant_ref: str,
        department_id: str,
        display_name: str,
        actor_ref: str,
        cost_center: str | None = None,
    ) -> Department:
        department = Department(
            department_id=department_id,
            display_name=display_name,
            cost_center=cost_center,
            updated_at=_now(),
            updated_by=actor_ref,
        )
        key = (tenant_ref, department.department_id)
        if self._repository.get_department(*key) is not None:
            raise ValueError("department already exists")
        return self._repository.save_department(tenant_ref, department)

    def update_department(
        self,
        *,
        tenant_ref: str,
        department_id: str,
        actor_ref: str,
        display_name: str | None = None,
        cost_center: str | None = None,
        status: str | None = None,
        base_version: int | None = None,
    ) -> Department:
        key = (tenant_ref, department_id)
        current = self._repository.get_department(*key)
        if current is None:
            raise KeyError(department_id)
        if base_version is not None and current.version != base_version:
            raise RuntimeError("department version conflict")
        payload = current.model_dump()
        if display_name is not None:
            payload["display_name"] = display_name
        if cost_center is not None:
            payload["cost_center"] = cost_center
        if status is not None:
            payload["status"] = status
        payload.update({"version": current.version + 1, "updated_at": _now(), "updated_by": actor_ref})
        updated = Department.model_validate(payload)
        return self._repository.save_department(tenant_ref, updated)

    def list_departments(self, *, tenant_ref: str) -> list[Department]:
        rows = self._repository.list_departments(tenant_ref)
        return sorted(rows, key=lambda item: item.display_name.lower())

    def assign_workspace(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        department_id: str | None,
        actor_ref: str,
    ) -> dict[str, Any]:
        if department_id is not None:
            department = self._repository.get_department(tenant_ref, department_id)
            if department is None or department.status != "active":
                raise ValueError("active department not found")
        self._repository.save_workspace_department(tenant_ref, workspace_id, department_id)
        return {
            "workspace_id": workspace_id,
            "department_id": department_id,
            "display_department": department_id or "unassigned",
            "updated_by": actor_ref,
            "updated_at": _now(),
        }

    def workspace_department(self, tenant_ref: str, workspace_id: str) -> str | None:
        return self._repository.get_workspace_department(tenant_ref, workspace_id)

    def create_price_card(
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        items: list[dict[str, Any]],
    ) -> PriceCardRevision:
        normalized = [PriceCardItem.model_validate(item) for item in items]
        if not normalized:
            raise ValueError("price card requires at least one item")
        deployments = [item.deployment for item in normalized]
        if len(set(deployments)) != len(deployments):
            raise ValueError("price card deployments must be unique")
        revision = PriceCardRevision(
            revision_id=f"price_{uuid4().hex}",
            status="draft",
            items=normalized,
            created_by=actor_ref,
            created_at=_now(),
        )
        return self._repository.save_price_card(tenant_ref, revision)

    def review_price_card(
        self,
        *,
        tenant_ref: str,
        revision_id: str,
        actor_ref: str,
    ) -> PriceCardRevision:
        revision = self._price_card(tenant_ref, revision_id)
        if revision.status != "draft":
            raise RuntimeError("only draft price cards may enter review")
        if revision.created_by == actor_ref:
            raise PermissionError("price card author cannot review the same revision")
        revision.status = "under_review"
        revision.reviewed_by = actor_ref
        revision.reviewed_at = _now()
        return self._save_price_card(tenant_ref, revision)

    def activate_price_card(
        self,
        *,
        tenant_ref: str,
        revision_id: str,
        actor_ref: str,
        actions_enabled: bool,
    ) -> PriceCardRevision:
        if not actions_enabled:
            raise PermissionError("FinOps production actions are disabled")
        revision = self._price_card(tenant_ref, revision_id)
        if revision.status != "under_review" or not revision.reviewed_by:
            raise RuntimeError("price card requires review before activation")
        for value in self._repository.list_price_cards(tenant_ref):
            if value.status == "active":
                self._repository.save_price_card(
                    tenant_ref, value.model_copy(update={"status": "retired"})
                )
        revision.status = "active"
        revision.activated_at = _now()
        return self._save_price_card(tenant_ref, revision)

    def list_price_cards(self, *, tenant_ref: str) -> list[PriceCardRevision]:
        rows = self._repository.list_price_cards(tenant_ref)
        return sorted(rows, key=lambda item: item.created_at, reverse=True)

    def get_price_card(
        self,
        *,
        tenant_ref: str,
        revision_id: str,
    ) -> PriceCardRevision:
        return self._price_card(tenant_ref, revision_id)

    def restore_price_card(
        self,
        *,
        tenant_ref: str,
        target_revision_id: str,
        previous_revision_id: str | None,
    ) -> None:
        target = self._price_card(tenant_ref, target_revision_id)
        self._repository.save_price_card(
            tenant_ref,
            target.model_copy(
                update={"status": "under_review", "activated_at": None}
            ),
        )
        if previous_revision_id:
            previous = self._price_card(tenant_ref, previous_revision_id)
            self._repository.save_price_card(
                tenant_ref,
                previous.model_copy(update={"status": "active"}),
            )

    def create_policy(
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        policy_type: str,
        configuration: dict[str, Any],
    ) -> FinOpsPolicy:
        model = _POLICY_MODELS.get(policy_type)
        if model is None:
            raise ValueError("unsupported FinOps policy type")
        try:
            normalized = model.model_validate(configuration).model_dump(mode="json")
        except ValidationError as exc:
            raise ValueError("invalid typed FinOps policy") from exc
        policy = FinOpsPolicy(
            policy_id=f"policy_{uuid4().hex}",
            policy_type=policy_type,
            configuration=normalized,
            updated_at=_now(),
            updated_by=actor_ref,
        )
        return self._repository.save_policy(tenant_ref, policy)

    def update_policy(
        self,
        *,
        tenant_ref: str,
        policy_id: str,
        actor_ref: str,
        configuration: dict[str, Any],
        status: str,
        base_version: int,
    ) -> FinOpsPolicy:
        current = self._repository.get_policy(tenant_ref, policy_id)
        if current is None:
            raise KeyError(policy_id)
        if current.version != base_version:
            raise RuntimeError("policy version conflict")
        model = _POLICY_MODELS[current.policy_type]
        try:
            normalized = model.model_validate(configuration).model_dump(mode="json")
            updated = FinOpsPolicy(
                **{
                    **current.model_dump(),
                    "configuration": normalized,
                    "status": status,
                    "version": current.version + 1,
                    "updated_at": _now(),
                    "updated_by": actor_ref,
                }
            )
        except ValidationError as exc:
            raise ValueError("invalid typed FinOps policy") from exc
        return self._repository.save_policy(tenant_ref, updated)

    def list_policies(self, *, tenant_ref: str) -> list[FinOpsPolicy]:
        rows = self._repository.list_policies(tenant_ref)
        return sorted(rows, key=lambda item: item.updated_at, reverse=True)

    def _price_card(self, tenant_ref: str, revision_id: str) -> PriceCardRevision:
        revision = self._repository.get_price_card(tenant_ref, revision_id)
        if revision is None:
            raise KeyError(revision_id)
        return revision.model_copy(deep=True)

    def _save_price_card(self, tenant_ref: str, revision: PriceCardRevision) -> PriceCardRevision:
        return self._repository.save_price_card(tenant_ref, revision)


def estimate_request_cost(tokens: TokenUsage, item: PriceCardItem) -> float | None:
    observed = {
        "input": tokens.input,
        "output": tokens.output,
        "cached": tokens.cached_input,
        "reasoning": tokens.reasoning,
    }
    prices = {
        "input": item.input_per_million,
        "output": item.output_per_million,
        "cached": item.cached_input_per_million,
        "reasoning": item.reasoning_per_million,
    }
    if any(observed[name] is not None and prices[name] is None for name in observed):
        return None
    cached = int(tokens.cached_input or 0)
    regular_input = max(0, int(tokens.input or 0) - cached)
    amount = (
        regular_input * float(item.input_per_million or 0)
        + int(tokens.output or 0) * float(item.output_per_million or 0)
        + cached * float(item.cached_input_per_million or 0)
        + int(tokens.reasoning or 0) * float(item.reasoning_per_million or 0)
    ) / 1_000_000
    return round(amount, 10)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
