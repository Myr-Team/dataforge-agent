from __future__ import annotations

import hashlib
import json
from typing import Callable

from .management import FinOpsManagementService


class ManagementPriceCardClient:
    def __init__(
        self,
        service_provider: Callable[[], FinOpsManagementService],
    ) -> None:
        self._service_provider = service_provider

    def current_version(self, tenant_ref: str, revision_id: str) -> str:
        revision = self._service_provider().get_price_card(
            tenant_ref=tenant_ref,
            revision_id=revision_id,
        )
        return price_card_version(revision.model_dump(mode="json"))

    def activate(self, tenant_ref: str, revision_id: str) -> dict[str, str | None]:
        service = self._service_provider()
        previous = next(
            (
                item
                for item in service.list_price_cards(tenant_ref=tenant_ref)
                if item.status == "active"
            ),
            None,
        )
        service.activate_price_card(
            tenant_ref=tenant_ref,
            revision_id=revision_id,
            actor_ref="finops-governance-action",
            actions_enabled=True,
        )
        return {
            "activated_revision_id": revision_id,
            "previous_revision_id": previous.revision_id if previous else None,
        }

    def verify_active(self, tenant_ref: str, revision_id: str) -> bool:
        return any(
            item.revision_id == revision_id and item.status == "active"
            for item in self._service_provider().list_price_cards(
                tenant_ref=tenant_ref
            )
        )

    def restore(self, tenant_ref: str, result: dict[str, object]) -> bool:
        target_revision_id = str(result.get("activated_revision_id") or "")
        previous_revision_id = str(result.get("previous_revision_id") or "") or None
        if not target_revision_id:
            return False
        service = self._service_provider()
        service.restore_price_card(
            tenant_ref=tenant_ref,
            target_revision_id=target_revision_id,
            previous_revision_id=previous_revision_id,
        )
        if previous_revision_id:
            return self.verify_active(tenant_ref, previous_revision_id)
        return not any(
            item.status == "active"
            for item in service.list_price_cards(tenant_ref=tenant_ref)
        )


def price_card_version(value: dict[str, object]) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
