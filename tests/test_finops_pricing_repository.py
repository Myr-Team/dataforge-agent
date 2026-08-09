from __future__ import annotations

import pytest

from backend.finops.sql_pricing import (
    DeploymentPriceMapping,
    InMemoryPriceMappingRepository,
    PriceMappingConflict,
)


def test_price_mapping_compare_and_swap_and_tenant_scope() -> None:
    repository = InMemoryPriceMappingRepository()
    created = repository.upsert(
        DeploymentPriceMapping(
            tenant_ref="tenant-a",
            deployment="gpt-5.6-terra",
            official_price_key="azure-openai:gpt-5.1:global-standard:global",
            mapping_revision=1,
            updated_by_ref="actor-a",
        ),
        base_revision=0,
    )

    assert created.mapping_revision == 1
    assert repository.get("tenant-a", "gpt-5.6-terra") == created
    assert repository.get("tenant-b", "gpt-5.6-terra") is None

    with pytest.raises(PriceMappingConflict):
        repository.upsert(
            created.model_copy(update={"mapping_revision": 2}),
            base_revision=0,
        )


def test_price_mapping_delete_restores_unpriced_and_is_tenant_scoped() -> None:
    repository = InMemoryPriceMappingRepository()
    repository.upsert(
        DeploymentPriceMapping(
            tenant_ref="tenant-a",
            deployment="gpt-5.6-terra",
            official_price_key="azure-openai:gpt-5.1:global-standard:global",
            mapping_revision=1,
            updated_by_ref="actor-a",
        ),
        base_revision=0,
    )

    # Deleting a mapping in another tenant must not touch tenant-a's row.
    assert repository.delete("tenant-b", "gpt-5.6-terra", base_revision=1) is False
    assert repository.get("tenant-a", "gpt-5.6-terra") is not None

    with pytest.raises(PriceMappingConflict):
        repository.delete("tenant-a", "gpt-5.6-terra", base_revision=2)

    assert repository.delete("tenant-a", "gpt-5.6-terra", base_revision=1) is True
    assert repository.get("tenant-a", "gpt-5.6-terra") is None
    # A subsequent create starts a fresh revision sequence.
    restored = repository.upsert(
        DeploymentPriceMapping(
            tenant_ref="tenant-a",
            deployment="gpt-5.6-terra",
            official_price_key="azure-openai:gpt-5.1:global-standard:global",
            mapping_revision=1,
            updated_by_ref="actor-a",
        ),
        base_revision=0,
    )
    assert restored.mapping_revision == 1
