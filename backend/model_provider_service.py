from __future__ import annotations

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
from .deepseek_provider import DeepSeekProvider, ProviderFailure, ProviderTransport
from .model_provider_repository import ModelProviderRepository
from .model_provider_secrets import ModelProviderSecretStore
from .model_providers import (
    ModelProviderRecord,
    ProviderModel,
    ProviderPatch,
    deepseek_api_endpoint,
)
from .provider_client import ProviderInvocation, ProviderMessage


class ProviderConfigurationError(ValueError):
    code = "provider_configuration_invalid"


_SERVER_OWNED_PATCH_FIELDS = frozenset({
    "connection_state",
    "governance_state",
    "available_models",
    "last_tested_at",
    "last_success_at",
    "safe_error_category",
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
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._transport = transport
        self._bedrock = bedrock_control_plane or Boto3BedrockControlPlane()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

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
        secret_value = self._secret_store.get(
            tenant_ref,
            provider_id,
            value.secret_ref,
        )
        return self._test_record(
            value,
            secret_value=secret_value,
            actor_ref=actor_ref,
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

    def _test_record(
        self,
        value: ModelProviderRecord,
        *,
        secret_value: str,
        actor_ref: str,
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
                DeepSeekProvider(transport=self._transport).invoke(
                    ProviderInvocation(
                        request_ref=f"test_{uuid.uuid4().hex[:24]}",
                        correlation_ref=f"test_{uuid.uuid4().hex[:24]}",
                        workspace_id="provider-connection-test",
                        agent_id=None,
                        execution_kind="connection_test",
                        model_id="deepseek-v4-flash",
                        messages=[
                            ProviderMessage(role="user", content="Reply with OK.")
                        ],
                        max_tokens=1,
                    ),
                    api_key=secret_value,
                    base_url=base_url,
                )
                models = _deepseek_models()
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


def _deepseek_models() -> list[ProviderModel]:
    capabilities = ["chat", "analysis", "tools", "json", "thinking"]
    return [
        ProviderModel(
            model_id="deepseek-v4-flash",
            display_name="DeepSeek V4 Flash",
            capabilities=capabilities,
            support_state="supported",
            price_key="deepseek:deepseek-v4-flash:official",
        ),
        ProviderModel(
            model_id="deepseek-v4-pro",
            display_name="DeepSeek V4 Pro",
            capabilities=capabilities,
            support_state="supported",
            price_key="deepseek:deepseek-v4-pro:official",
        ),
    ]


def _secret_value(secret_value: str | None, api_key: str | None) -> str:
    if secret_value is not None:
        return secret_value
    if api_key is not None:
        return api_key
    raise ValueError("secret_value is required")


__all__ = ["ModelProviderService", "ProviderConfigurationError"]
