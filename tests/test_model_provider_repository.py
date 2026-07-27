from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.model_provider_repository import (
    InMemoryModelProviderRepository,
    ModelProviderConflictError,
    ModelProviderNotFoundError,
)
from backend.model_providers import ModelProviderRecord, ProviderPatch


def _record(*, tenant_ref: str = "tenant-a") -> ModelProviderRecord:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    return ModelProviderRecord(
        provider_id="provider_01",
        tenant_ref=tenant_ref,
        provider_type="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
        secret_ref="kv:provider-secret-reference",
        connection_state="testing",
        governance_state="pending",
        available_models=[],
        revision=1,
        created_by_ref="actor-a",
        updated_by_ref="actor-a",
        created_at=now,
        updated_at=now,
    )


def test_repository_isolates_identical_provider_ids_by_tenant() -> None:
    repository = InMemoryModelProviderRepository()
    repository.create(_record(tenant_ref="tenant-a"))
    repository.create(_record(tenant_ref="tenant-b"))

    assert [item.tenant_ref for item in repository.list("tenant-a")] == ["tenant-a"]
    assert [item.tenant_ref for item in repository.list("tenant-b")] == ["tenant-b"]

    with pytest.raises(ModelProviderNotFoundError):
        repository.get("tenant-c", "provider_01")


def test_repository_rejects_stale_provider_revision_without_mutation() -> None:
    repository = InMemoryModelProviderRepository()
    repository.create(_record())

    saved = repository.update(
        "tenant-a",
        "provider_01",
        ProviderPatch(base_revision=1, display_name="DeepSeek primary"),
        actor_ref="actor-b",
    )
    assert saved.revision == 2
    assert saved.display_name == "DeepSeek primary"

    with pytest.raises(ModelProviderConflictError):
        repository.update(
            "tenant-a",
            "provider_01",
            ProviderPatch(base_revision=1, display_name="stale overwrite"),
            actor_ref="actor-c",
        )

    current = repository.get("tenant-a", "provider_01")
    assert current.revision == 2
    assert current.display_name == "DeepSeek primary"


def test_repository_never_accepts_api_key_material() -> None:
    repository = InMemoryModelProviderRepository()

    with pytest.raises(TypeError):
        repository.create(_record(), api_key="forbidden-key-material")  # type: ignore[call-arg]
