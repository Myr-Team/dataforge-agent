from __future__ import annotations

from datetime import datetime, timezone

import backend.model_provider_runtime as provider_runtime
from backend.model_provider_repository import InMemoryModelProviderRepository
from backend.model_providers import ModelProviderRecord, ProviderModel


class _Secrets:
    def status(self, _tenant_ref: str, _provider_id: str, _secret_ref: str) -> str:
        return "stored"

    def get(self, _tenant_ref: str, _provider_id: str, _secret_ref: str) -> str:
        return "secret-marker"


def _repository() -> InMemoryModelProviderRepository:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    repository = InMemoryModelProviderRepository()
    repository.create(
        ModelProviderRecord(
            provider_id="provider_primary",
            tenant_ref="tenant-safe",
            provider_type="deepseek",
            display_name="DeepSeek 原厂",
            base_url="https://api.deepseek.com",
            secret_ref="kv:provider-primary",
            connection_state="connected",
            governance_state="governed",
            available_models=[
                ProviderModel(
                    model_id="deepseek-v4-pro",
                    display_name="DeepSeek V4 Pro",
                    capabilities=["chat", "analysis"],
                    support_state="supported",
                    price_key="deepseek:deepseek-v4-pro:official",
                )
            ],
            last_tested_at=now,
            last_success_at=now,
            revision=4,
            created_by_ref="actor-safe",
            updated_by_ref="actor-safe",
            created_at=now,
            updated_at=now,
        )
    )
    return repository


def test_actor_runtime_loads_only_the_trusted_tenant_and_scopes_secret_access(monkeypatch) -> None:
    monkeypatch.setenv("DF_PROVIDER_CONNECTORS_ENABLED", "1")
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "hmac-safe")
    monkeypatch.setattr(provider_runtime, "canonical_tenant_ref", lambda *_args, **_kwargs: "tenant-safe")
    monkeypatch.setattr(provider_runtime, "get_model_provider_repository", _repository)
    monkeypatch.setattr(provider_runtime, "get_model_provider_secret_store", _Secrets)

    runtime = provider_runtime.load_actor_provider_runtime(
        {
            "source": "easy_auth",
            "actor_id": "actor-object-id",
            "tenant_id": "tenant-object-id",
            "email": "owner@contoso.com",
        }
    )

    assert len(runtime.routes) == 1
    assert runtime.routes[0].provider_id == "provider_primary"
    with provider_runtime.provider_runtime_scope(runtime.connections):
        connection = provider_runtime.current_provider_connection("provider_primary")
        assert connection is not None
        assert provider_runtime.runtime_provider_secret(connection) == "secret-marker"
    assert provider_runtime.current_provider_connection("provider_primary") is None


def test_actor_runtime_ignores_untrusted_or_disabled_identity(monkeypatch) -> None:
    monkeypatch.setenv("DF_PROVIDER_CONNECTORS_ENABLED", "1")
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "0")

    runtime = provider_runtime.load_actor_provider_runtime(
        {
            "source": "client_actor",
            "actor_id": "browser-id",
            "tenant_id": "browser-tenant",
        }
    )

    assert runtime.routes == ()
    assert runtime.connections == ()
