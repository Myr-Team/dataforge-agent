from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from .deepseek_provider import DeepSeekProvider, ProviderFailure, ProviderTransport
from .model_provider_repository import ModelProviderRepository
from .model_provider_secrets import ModelProviderSecretStore
from .model_providers import (
    ModelProviderRecord,
    ProviderModel,
    ProviderPatch,
)
from .provider_client import ProviderInvocation, ProviderMessage


class ModelProviderService:
    def __init__(
        self,
        *,
        repository: ModelProviderRepository,
        secret_store: ModelProviderSecretStore,
        transport: ProviderTransport,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list(self, tenant_ref: str) -> list[dict[str, object]]:
        return [item.public_payload() for item in self._repository.list(tenant_ref)]

    def create(
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        provider_type: str,
        display_name: str,
        base_url: str,
        api_key: str,
        provider_id: str | None = None,
    ) -> dict[str, object]:
        identifier = provider_id or f"provider_{uuid.uuid4().hex[:24]}"
        secret_ref = self._secret_store.put(
            tenant_ref,
            identifier,
            api_key,
        )
        now = self._clock()
        value = self._repository.create(
            ModelProviderRecord(
                provider_id=identifier,
                tenant_ref=tenant_ref,
                provider_type=provider_type,
                display_name=display_name,
                base_url=base_url,
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
        return self._test_record(value, api_key=api_key, actor_ref=actor_ref)

    def update(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        patch: ProviderPatch,
        actor_ref: str,
    ) -> dict[str, object]:
        return self._repository.update(
            tenant_ref,
            provider_id,
            patch,
            actor_ref=actor_ref,
        ).public_payload()

    def test(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        actor_ref: str,
    ) -> dict[str, object]:
        value = self._repository.get(tenant_ref, provider_id)
        api_key = self._secret_store.get(
            tenant_ref,
            provider_id,
            value.secret_ref,
        )
        return self._test_record(value, api_key=api_key, actor_ref=actor_ref)

    def rotate(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        api_key: str,
        base_revision: int,
        actor_ref: str,
    ) -> dict[str, object]:
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
            ).public_payload()
        secret_ref = self._secret_store.rotate(
            tenant_ref,
            provider_id,
            api_key,
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
        return self._test_record(updated, api_key=api_key, actor_ref=actor_ref)

    def disable(
        self,
        *,
        tenant_ref: str,
        provider_id: str,
        base_revision: int,
        actor_ref: str,
    ) -> dict[str, object]:
        return self._repository.update(
            tenant_ref,
            provider_id,
            ProviderPatch(
                base_revision=base_revision,
                connection_state="disabled",
            ),
            actor_ref=actor_ref,
        ).public_payload()

    def _test_record(
        self,
        value: ModelProviderRecord,
        *,
        api_key: str,
        actor_ref: str,
    ) -> dict[str, object]:
        tested_at = self._clock()
        try:
            DeepSeekProvider(transport=self._transport).invoke(
                ProviderInvocation(
                    request_ref=f"test_{uuid.uuid4().hex[:24]}",
                    correlation_ref=f"test_{uuid.uuid4().hex[:24]}",
                    workspace_id="provider-connection-test",
                    agent_id=None,
                    execution_kind="connection_test",
                    model_id="deepseek-v4-flash",
                    messages=[
                        ProviderMessage(
                            role="user",
                            content="Reply with OK.",
                        )
                    ],
                    max_tokens=1,
                ),
                api_key=api_key,
                base_url=value.base_url,
            )
            patch = ProviderPatch(
                base_revision=value.revision,
                connection_state="connected",
                available_models=_deepseek_models(),
                last_tested_at=tested_at,
                last_success_at=tested_at,
                safe_error_category=None,
            )
        except ProviderFailure as exc:
            state = (
                "invalid"
                if exc.category
                in {
                    "authentication_failed",
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
        return updated.public_payload()


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


__all__ = ["ModelProviderService"]
