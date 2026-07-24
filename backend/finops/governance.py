from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


ActionType = Literal[
    "apim_token_limit",
    "model_route",
    "cache_policy",
    "price_card_activation",
]
ActionStatus = Literal[
    "draft",
    "pending_approval",
    "approved",
    "executing",
    "verifying",
    "succeeded",
    "failed",
    "rolled_back",
    "rollback_failed",
]


class ActionPermissionDenied(PermissionError):
    pass


class ActionConflict(RuntimeError):
    pass


class ActionNotFound(KeyError):
    pass


class ApimTokenLimitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=160)
    quota_tokens: int = Field(gt=0)
    window_seconds: Literal[60, 3600, 86400]
    rate_limit: int | None = Field(default=None, gt=0)
    base_version: str = Field(min_length=1, max_length=128)


class ModelRoutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=160)
    route_id: str = Field(min_length=1, max_length=128)
    deployment: str = Field(min_length=1, max_length=160)
    execution_kind: Literal[
        "direct_reply",
        "follow_up",
        "full_analysis",
        "audit_repair",
    ] = "full_analysis"
    base_version: str = Field(min_length=1, max_length=128)


class CachePolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=160)
    enabled: bool
    ttl_seconds: int = Field(ge=30, le=86400)
    base_version: str = Field(min_length=1, max_length=128)


class PriceCardActivationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(min_length=1, max_length=128)
    base_version: str = Field(min_length=1, max_length=128)


_PAYLOAD_TYPES: dict[str, type[BaseModel]] = {
    "apim_token_limit": ApimTokenLimitPayload,
    "model_route": ModelRoutePayload,
    "cache_policy": CachePolicyPayload,
    "price_card_activation": PriceCardActivationPayload,
}


class ActionTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_status: ActionStatus | None
    to_status: ActionStatus
    actor_ref: str
    reason: str | None = None
    occurred_at: str


class GovernanceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    tenant_ref: str
    action_type: ActionType
    status: ActionStatus
    payload: dict[str, Any]
    proposed_by: str
    approved_by: str | None = None
    result: dict[str, Any] | None = None
    version: int = 1
    created_at: str
    updated_at: str
    transitions: list[ActionTransition] = Field(default_factory=list)


class ActionExecutor(Protocol):
    def read_version(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> str: ...
    def execute(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> dict[str, Any]: ...
    def verify(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool: ...
    def rollback(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool: ...


class InMemoryActionRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._actions: dict[tuple[str, str], GovernanceAction] = {}

    def save(self, action: GovernanceAction) -> GovernanceAction:
        with self._lock:
            self._actions[(action.tenant_ref, action.action_id)] = action.model_copy(deep=True)
        return action.model_copy(deep=True)

    def get(self, tenant_ref: str, action_id: str) -> GovernanceAction | None:
        with self._lock:
            action = self._actions.get((tenant_ref, action_id))
        return action.model_copy(deep=True) if action else None

    def list(self, tenant_ref: str) -> list[GovernanceAction]:
        with self._lock:
            rows = [value.model_copy(deep=True) for (tenant, _), value in self._actions.items() if tenant == tenant_ref]
        return sorted(rows, key=lambda item: (item.created_at, item.action_id), reverse=True)


class ActionRepository(Protocol):
    def save(self, action: GovernanceAction) -> GovernanceAction: ...
    def get(self, tenant_ref: str, action_id: str) -> GovernanceAction | None: ...
    def list(self, tenant_ref: str) -> list[GovernanceAction]: ...


class FinOpsActionService:
    def __init__(
        self,
        *,
        repository: ActionRepository,
        executors: dict[str, ActionExecutor],
    ) -> None:
        self._repository = repository
        self._executors = dict(executors)

    def create(
        self,
        *,
        tenant_ref: str,
        action_type: ActionType,
        payload: dict[str, Any],
        actor_ref: str,
        actor_kind: Literal["human", "agent"] = "human",
    ) -> GovernanceAction:
        if actor_kind != "human":
            raise ActionPermissionDenied("agents may recommend actions but cannot create approval records")
        clean_payload = _validate_payload(action_type, payload)
        now = _now()
        action = GovernanceAction(
            action_id=f"action_{uuid4().hex}",
            tenant_ref=tenant_ref,
            action_type=action_type,
            status="draft",
            payload=clean_payload,
            proposed_by=actor_ref,
            created_at=now,
            updated_at=now,
            transitions=[
                ActionTransition(
                    from_status=None,
                    to_status="draft",
                    actor_ref=actor_ref,
                    occurred_at=now,
                )
            ],
        )
        return self._repository.save(action)

    def submit(self, action_id: str, *, tenant_ref: str, actor_ref: str) -> GovernanceAction:
        action = self._require(tenant_ref, action_id)
        if action.status != "draft":
            raise ActionConflict("only draft actions may be submitted")
        return self._transition(action, "pending_approval", actor_ref)

    def approve(self, action_id: str, *, tenant_ref: str, actor_ref: str) -> GovernanceAction:
        action = self._require(tenant_ref, action_id)
        if action.status != "pending_approval":
            raise ActionConflict("action is not pending approval")
        if actor_ref == action.proposed_by:
            raise ActionPermissionDenied("production actions require a different approver")
        action.approved_by = actor_ref
        return self._transition(action, "approved", actor_ref)

    def execute(
        self,
        action_id: str,
        *,
        tenant_ref: str,
        actor_ref: str,
        actions_enabled: bool,
    ) -> GovernanceAction:
        if not actions_enabled:
            raise ActionPermissionDenied("FinOps production actions are disabled")
        action = self._require(tenant_ref, action_id)
        if action.status != "approved":
            raise ActionConflict("only approved actions may execute")
        executor = self._executor(action)
        expected = str(action.payload.get("base_version") or "")
        actual = str(
            executor.read_version(action.payload, tenant_ref=action.tenant_ref) or ""
        )
        if expected != actual:
            raise ActionConflict("configuration drift detected; resubmit for approval")
        action = self._transition(action, "executing", actor_ref)
        try:
            action.result = executor.execute(
                action.payload,
                tenant_ref=action.tenant_ref,
            )
        except Exception as exc:
            action.result = {"status": "failed", "category": type(exc).__name__}
            return self._transition(action, "failed", actor_ref)
        return self._transition(action, "verifying", actor_ref)

    def verify(self, action_id: str, *, tenant_ref: str, actor_ref: str) -> GovernanceAction:
        action = self._require(tenant_ref, action_id)
        if action.status != "verifying":
            raise ActionConflict("action is not awaiting verification")
        succeeded = self._executor(action).verify(
            action.payload,
            action.result,
            tenant_ref=action.tenant_ref,
        )
        return self._transition(action, "succeeded" if succeeded else "failed", actor_ref)

    def rollback(
        self,
        action_id: str,
        *,
        tenant_ref: str,
        actor_ref: str,
        reason: str,
        owner: bool,
    ) -> GovernanceAction:
        action = self._require(tenant_ref, action_id)
        if not owner or not str(reason or "").strip():
            raise ActionPermissionDenied("emergency rollback requires owner role and a reason")
        if action.status not in {"succeeded", "failed"}:
            raise ActionConflict("only executed actions may be rolled back")
        succeeded = self._executor(action).rollback(
            action.payload,
            action.result,
            tenant_ref=action.tenant_ref,
        )
        return self._transition(
            action,
            "rolled_back" if succeeded else "rollback_failed",
            actor_ref,
            reason=str(reason).strip()[:512],
        )

    def list(self, *, tenant_ref: str) -> list[GovernanceAction]:
        return self._repository.list(tenant_ref)

    def get(self, *, tenant_ref: str, action_id: str) -> GovernanceAction:
        return self._require(tenant_ref, action_id)

    def _require(self, tenant_ref: str, action_id: str) -> GovernanceAction:
        action = self._repository.get(tenant_ref, action_id)
        if action is None:
            raise ActionNotFound(action_id)
        return action

    def _executor(self, action: GovernanceAction) -> ActionExecutor:
        executor = self._executors.get(action.action_type)
        if executor is None:
            raise ActionConflict("typed executor is unavailable")
        return executor

    def _transition(
        self,
        action: GovernanceAction,
        status: ActionStatus,
        actor_ref: str,
        *,
        reason: str | None = None,
    ) -> GovernanceAction:
        now = _now()
        previous = action.status
        action.status = status
        action.updated_at = now
        action.version += 1
        action.transitions.append(
            ActionTransition(
                from_status=previous,
                to_status=status,
                actor_ref=actor_ref,
                reason=reason,
                occurred_at=now,
            )
        )
        return self._repository.save(action)


class RecordingExecutor:
    """Safe test/candidate executor; it never calls an external control plane."""

    def __init__(self, *, current_version: str) -> None:
        self.current_version = current_version
        self.calls: list[str] = []

    def read_version(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> str:
        return self.current_version

    def execute(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> dict[str, Any]:
        self.calls.append("execute")
        return {"candidate": True, "version_before": self.current_version}

    def verify(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        self.calls.append("verify")
        return True

    def rollback(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        self.calls.append("rollback")
        return True


def _validate_payload(action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = _PAYLOAD_TYPES.get(action_type)
    if model is None:
        raise ValueError("unsupported action type")
    try:
        return model.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise ValueError("invalid typed action payload") from exc


def validate_typed_action_payload(
    action_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate an allowlisted action without creating or executing it."""
    return _validate_payload(action_type, payload)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
