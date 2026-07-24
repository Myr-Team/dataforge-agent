from __future__ import annotations

from typing import Any, Protocol

from .governance import (
    ApimTokenLimitPayload,
    CachePolicyPayload,
    ModelRoutePayload,
    PriceCardActivationPayload,
)


class ApimPolicyClient(Protocol):
    def current_version(self, workspace_id: str) -> str: ...
    def create_candidate(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def smoke_candidate(self, workspace_id: str, revision_id: str) -> dict[str, Any]: ...
    def read_policy_hash(self, workspace_id: str, revision_id: str) -> str: ...
    def activate_candidate(self, workspace_id: str, revision_id: str) -> None: ...
    def active_revision(self, workspace_id: str) -> str: ...
    def activate_revision(self, workspace_id: str, revision_id: str) -> None: ...


class ApimPolicyExecutor:
    """Typed APIM candidate-revision executor.

    The client owns Azure SDK/REST calls. This adapter accepts only the bounded
    quota/rate payload, requires MI=200 and anonymous=401 smoke evidence, and
    compares the read-back policy hash before activation.
    """

    def __init__(self, client: ApimPolicyClient) -> None:
        self._client = client

    def read_version(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> str:
        clean = ApimTokenLimitPayload.model_validate(payload)
        return str(self._client.current_version(clean.workspace_id))

    def execute(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> dict[str, Any]:
        clean_model = ApimTokenLimitPayload.model_validate(payload)
        clean = clean_model.model_dump(mode="json")
        candidate = self._client.create_candidate(clean)
        revision_id = str(candidate.get("revision_id") or "")
        previous_revision_id = str(candidate.get("previous_revision_id") or "")
        expected_hash = str(candidate.get("policy_hash") or "")
        if not revision_id or not previous_revision_id or not expected_hash:
            raise RuntimeError("APIM candidate evidence is incomplete")
        workspace_id = clean_model.workspace_id
        smoke = self._client.smoke_candidate(workspace_id, revision_id)
        if int(smoke.get("managed_identity_status") or 0) != 200:
            raise RuntimeError("APIM managed identity candidate smoke failed")
        if int(smoke.get("anonymous_status") or 0) != 401:
            raise RuntimeError("APIM anonymous candidate smoke failed")
        readback_hash = str(self._client.read_policy_hash(workspace_id, revision_id) or "")
        if readback_hash != expected_hash:
            raise RuntimeError("APIM policy hash read-back mismatch")
        return {
            "candidate_revision_id": revision_id,
            "previous_revision_id": previous_revision_id,
            "policy_hash": expected_hash,
            "managed_identity_status": 200,
            "anonymous_status": 401,
            "candidate_verified": True,
            "activated": False,
        }

    def verify(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        clean = ApimTokenLimitPayload.model_validate(payload)
        revision_id = str((result or {}).get("candidate_revision_id") or "")
        if not revision_id or (result or {}).get("candidate_verified") is not True:
            return False
        self._client.activate_candidate(clean.workspace_id, revision_id)
        return str(self._client.active_revision(clean.workspace_id) or "") == revision_id

    def rollback(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        clean = ApimTokenLimitPayload.model_validate(payload)
        previous = str((result or {}).get("previous_revision_id") or "")
        if not previous:
            return False
        self._client.activate_revision(clean.workspace_id, previous)
        return str(self._client.active_revision(clean.workspace_id) or "") == previous


class VersionedConfigClient(Protocol):
    def current_version(self, workspace_id: str) -> str: ...
    def apply(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def verify(self, payload: dict[str, Any], result: dict[str, Any]) -> bool: ...
    def restore(self, payload: dict[str, Any], result: dict[str, Any]) -> bool: ...


class VersionedConfigExecutor:
    """Shared executor for DataForge model-route and Redis cache policies."""

    def __init__(self, client: VersionedConfigClient, *, kind: str) -> None:
        if kind not in {"model_route", "cache_policy"}:
            raise ValueError("unsupported versioned DataForge config kind")
        self._client = client
        self._model = ModelRoutePayload if kind == "model_route" else CachePolicyPayload

    def _clean(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._model.model_validate(payload).model_dump(mode="json")

    def read_version(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> str:
        clean = self._clean(payload)
        return str(self._client.current_version(str(clean["workspace_id"])))

    def execute(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> dict[str, Any]:
        clean = self._clean(payload)
        result = self._client.apply(clean)
        if not isinstance(result, dict) or not result.get("previous"):
            raise RuntimeError("DataForge candidate result lacks rollback evidence")
        return result

    def verify(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        return bool(result and self._client.verify(self._clean(payload), result))

    def rollback(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        return bool(result and self._client.restore(self._clean(payload), result))


class PriceCardClient(Protocol):
    def current_version(self, tenant_ref: str, revision_id: str) -> str: ...
    def activate(self, tenant_ref: str, revision_id: str) -> dict[str, Any]: ...
    def verify_active(self, tenant_ref: str, revision_id: str) -> bool: ...
    def restore(self, tenant_ref: str, result: dict[str, Any]) -> bool: ...


class PriceCardActivationExecutor:
    def __init__(self, client: PriceCardClient) -> None:
        self._client = client

    def read_version(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> str:
        clean = PriceCardActivationPayload.model_validate(payload)
        return str(self._client.current_version(_require_tenant(tenant_ref), clean.revision_id))

    def execute(self, payload: dict[str, Any], *, tenant_ref: str | None = None) -> dict[str, Any]:
        clean = PriceCardActivationPayload.model_validate(payload)
        result = self._client.activate(_require_tenant(tenant_ref), clean.revision_id)
        if not isinstance(result, dict) or "previous_revision_id" not in result:
            raise RuntimeError("price-card activation lacks rollback evidence")
        return result

    def verify(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        clean = PriceCardActivationPayload.model_validate(payload)
        return self._client.verify_active(_require_tenant(tenant_ref), clean.revision_id)

    def rollback(
        self,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        tenant_ref: str | None = None,
    ) -> bool:
        return bool(result and self._client.restore(_require_tenant(tenant_ref), result))


def _require_tenant(value: str | None) -> str:
    tenant_ref = str(value or "").strip()
    if not tenant_ref:
        raise RuntimeError("tenant context is required for price-card governance")
    return tenant_ref
