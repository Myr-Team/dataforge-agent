from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from .aws_bedrock_provider import (
    AwsBedrockControlPlane,
    AwsBedrockCredential,
    BedrockConnectionFailure,
    Boto3BedrockControlPlane,
    bedrock_control_endpoint,
)
from .deepseek_provider import ProviderFailure, ProviderTransport
from .model_provider_repository import ModelProviderRepository
from .model_provider_secrets import ModelProviderSecretError, ModelProviderSecretStore
from .model_providers import (
    ModelProviderRecord,
    ProviderPatch,
    deepseek_api_endpoint,
    provider_route_eligibility,
)
from .provider_connection_probe import DeepSeekConnectionProbe


class ProviderConfigurationError(ValueError):
    def __init__(self, code: str = "provider_configuration_invalid") -> None:
        self.code = str(code or "provider_configuration_invalid")
        super().__init__(self.code)


_SERVER_OWNED_PATCH_FIELDS = frozenset({
    "connection_state",
    "governance_state",
    "available_models",
    "last_tested_at",
    "last_success_at",
    "safe_error_category",
    "connection_stage",
    "stage_durations_ms",
})


class ModelProviderService:
    def __init__(
        self,
        *,
        repository: ModelProviderRepository,
        secret_store: ModelProviderSecretStore,
        transport: ProviderTransport,
        bedrock_control_plane: AwsBedrockControlPlane | None = None,
        clock: Callable[[], datetime] | None = None,
        duration_clock: Callable[[], float] | None = None,
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._transport = transport
        self._bedrock = bedrock_control_plane or Boto3BedrockControlPlane()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._duration_clock = duration_clock or time.monotonic
        self._deepseek_probe = DeepSeekConnectionProbe(transport=transport)

    def list(self, tenant_ref: str) -> list[dict[str, object]]:
        return [
            item.public_payload(secret_status=self._secret_status(item))
            for item in self._repository.list(tenant_ref)
        ]

    def create(
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        provider_type: str,
        display_name: str,
        base_url: str,
        secret_value: str | None = None,
        region: str | None = None,
        provider_id: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, object]:
        secret_value = _secret_value(secret_value, api_key)
        identifier = provider_id or f"provider_{uuid.uuid4().hex[:24]}"
        secret_ref = self._secret_store.put(
            tenant_ref,
            identifier,
            secret_value,
        )
        now = self._clock()
        value = self._repository.create(
            ModelProviderRecord(
                provider_id=identifier,
                tenant_ref=tenant_ref,
                provider_type=provider_type,
                display_name=display_name,
                base_url=base_url,
                region=region,
                secret_ref=secret_ref,
                connection_state="testing",
                governance_state="pending",
                available_models=[],
                revision=1,
                created_by_ref=actor_ref,
                updated_by_ref=actor_ref,
                created_at=now,
                updated_at=now,
            )
        )
        return self._test_record(
            value,
            secret_value=secret_value,
            actor_ref=actor_ref,
            secret_read_ms=0,
        )

    def update(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        patch: ProviderPatch,
        actor_ref: str,
    ) -> dict[str, object]:
        value = self._repository.get(tenant_ref, provider_id)
        prepared = self.prepare_configuration_patch(value, patch)
        updated = self._repository.update(
            tenant_ref,
            provider_id,
            prepared,
            actor_ref=actor_ref,
        )
        return updated.public_payload(secret_status=self._secret_status(updated))

    def prepare_configuration_patch(
        self,
        value: ModelProviderRecord,
        patch: ProviderPatch,
    ) -> ProviderPatch:
        if patch.model_fields_set & _SERVER_OWNED_PATCH_FIELDS:
            raise ProviderConfigurationError()
        changes = patch.model_dump(
            exclude={"base_revision"},
            exclude_none=True,
        )
        if value.provider_type == "deepseek":
            if patch.region is not None:
                raise ProviderConfigurationError()
            if patch.base_url is not None:
                try:
                    changes["base_url"] = deepseek_api_endpoint(patch.base_url)
                except ValueError:
                    raise ProviderConfigurationError() from None
        elif value.provider_type == "aws_bedrock":
            if patch.base_url is not None:
                raise ProviderConfigurationError()
            if patch.region is not None:
                try:
                    normalized_region = str(patch.region).strip().lower()
                    changes["base_url"] = bedrock_control_endpoint(
                        normalized_region
                    )
                except ValueError:
                    raise ProviderConfigurationError() from None
                changes["region"] = normalized_region
        else:
            raise ProviderConfigurationError()
        return ProviderPatch.model_validate(
            {"base_revision": patch.base_revision, **changes}
        )

    def test(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        actor_ref: str,
    ) -> dict[str, object]:
        value = self._repository.get(tenant_ref, provider_id)
        secret_started = self._duration_clock()
        try:
            secret_value = self._secret_store.get(
                tenant_ref,
                provider_id,
                value.secret_ref,
            )
        except ModelProviderSecretError as exc:
            secret_read_ms = max(
                0,
                int((self._duration_clock() - secret_started) * 1000),
            )
            updated = self._repository.update(
                tenant_ref,
                provider_id,
                ProviderPatch(
                    base_revision=value.revision,
                    connection_state="invalid",
                    connection_stage="secret_read",
                    stage_durations_ms={"secret_read": secret_read_ms},
                    last_tested_at=self._clock(),
                    safe_error_category=exc.code,
                ),
                actor_ref=actor_ref,
            )
            return updated.public_payload(
                secret_status=self._secret_status(updated)
            )
        secret_read_ms = max(
            0,
            int((self._duration_clock() - secret_started) * 1000),
        )
        return self._test_record(
            value,
            secret_value=secret_value,
            actor_ref=actor_ref,
            secret_read_ms=secret_read_ms,
        )

    def rotate(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        secret_value: str | None = None,
        base_revision: int,
        actor_ref: str,
        api_key: str | None = None,
    ) -> dict[str, object]:
        secret_value = _secret_value(secret_value, api_key)
        value = self._repository.get(tenant_ref, provider_id)
        if value.revision != base_revision:
            return self._repository.update(
                tenant_ref,
                provider_id,
                ProviderPatch(
                    base_revision=base_revision,
                    connection_state=value.connection_state,
                ),
                actor_ref=actor_ref,
            ).public_payload(secret_status=self._secret_status(value))
        secret_ref = self._secret_store.rotate(
            tenant_ref,
            provider_id,
            secret_value,
        )
        updated = self._repository.update(
            tenant_ref,
            provider_id,
            ProviderPatch(
                base_revision=base_revision,
                connection_state="testing",
                safe_error_category=None,
            ),
            actor_ref=actor_ref,
        )
        if secret_ref != updated.secret_ref:
            raise RuntimeError("provider secret reference changed unexpectedly")
        return self._test_record(
            updated,
            secret_value=secret_value,
            actor_ref=actor_ref,
            secret_read_ms=0,
        )

    def disable(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        base_revision: int,
        actor_ref: str,
    ) -> dict[str, object]:
        updated = self._repository.update(
            tenant_ref,
            provider_id,
            ProviderPatch(
                base_revision=base_revision,
                connection_state="disabled",
            ),
            actor_ref=actor_ref,
        )
        return updated.public_payload(secret_status=self._secret_status(updated))

    def prepare_governance_patch(
        self,
        value: ModelProviderRecord,
        *,
        target_state: str,
    ) -> ProviderPatch:
        if target_state not in {"governed", "pending"}:
            raise ProviderConfigurationError()
        secret_status = self._secret_status(value)
        if target_state == "governed":
            eligibility = provider_route_eligibility(
                value,
                secret_status=secret_status,
            )
            if not eligibility["can_govern"] and not eligibility["selectable"]:
                raise ProviderConfigurationError(str(eligibility["reason"]))
        return ProviderPatch(
            base_revision=value.revision,
            governance_state=target_state,
        )

    def public_payload(self, value: ModelProviderRecord) -> dict[str, object]:
        return value.public_payload(secret_status=self._secret_status(value))

    def _test_record(
        self,
        value: ModelProviderRecord,
        *,
        secret_value: str,
        actor_ref: str,
        secret_read_ms: int = 0,
    ) -> dict[str, object]:
        tested_at = self._clock()
        try:
            if value.provider_type == "aws_bedrock":
                try:
                    credential = AwsBedrockCredential.from_secret_value(secret_value)
                except ValueError:
                    raise BedrockConnectionFailure("configuration_conflict") from None
                models = self._bedrock.list_models(value.region or "", credential)
                governance_state = "unmanaged"
            elif value.provider_type == "deepseek":
                try:
                    base_url = deepseek_api_endpoint(value.base_url)
                except ValueError:
                    raise ProviderFailure(
                        "configuration_conflict",
                        retryable=False,
                    ) from None
                result = self._deepseek_probe.run(
                    api_key=secret_value,
                    base_url=base_url,
                    secret_read_ms=secret_read_ms,
                )
                if result.safe_error_category:
                    raise ProviderFailure(
                        result.safe_error_category,
                        retryable=result.safe_error_category
                        in {
                            "provider_timeout",
                            "provider_unavailable",
                            "rate_limited",
                        },
                    )
                models = result.models
                governance_state = value.governance_state
            else:
                raise BedrockConnectionFailure("configuration_conflict")
            patch = ProviderPatch(
                base_revision=value.revision,
                connection_state="connected",
                governance_state=governance_state,
                available_models=models,
                last_tested_at=tested_at,
                last_success_at=tested_at,
                safe_error_category=None,
                connection_stage=(
                    result.connection_stage
                    if value.provider_type == "deepseek"
                    else "completed"
                ),
                stage_durations_ms=(
                    result.stage_durations_ms
                    if value.provider_type == "deepseek"
                    else {}
                ),
            )
        except (BedrockConnectionFailure, ProviderFailure) as exc:
            state = (
                "invalid"
                if exc.category
                in {
                    "authentication_failed",
                    "access_denied",
                    "configuration_conflict",
                    "insufficient_balance",
                    "invalid_request",
                    "invalid_parameters",
                }
                else "degraded"
            )
            patch = ProviderPatch(
                base_revision=value.revision,
                connection_state=state,
                last_tested_at=tested_at,
                safe_error_category=exc.category,
                connection_stage=(
                    result.connection_stage
                    if value.provider_type == "deepseek" and "result" in locals()
                    else "secret_read"
                ),
                stage_durations_ms=(
                    result.stage_durations_ms
                    if value.provider_type == "deepseek" and "result" in locals()
                    else {"secret_read": max(0, secret_read_ms)}
                ),
            )
        updated = self._repository.update(
            value.tenant_ref,
            value.provider_id,
            patch,
            actor_ref=actor_ref,
        )
        return updated.public_payload(secret_status="stored")

    def _secret_status(self, value: ModelProviderRecord) -> str:
        status = getattr(self._secret_store, "status", None)
        if not callable(status):
            return "unavailable"
        try:
            observed = status(
                value.tenant_ref,
                value.provider_id,
                value.secret_ref,
            )
        except Exception:
            return "unavailable"
        return observed if observed in {"stored", "missing", "unavailable"} else "unavailable"


def _secret_value(secret_value: str | None, api_key: str | None) -> str:
    if secret_value is not None:
        return secret_value
    if api_key is not None:
        return api_key
    raise ValueError("secret_value is required")


__all__ = ["ModelProviderService", "ProviderConfigurationError"]
