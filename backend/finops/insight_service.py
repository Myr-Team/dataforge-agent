from __future__ import annotations

from typing import Any, Mapping

from .insight_repository import InsightPage
from .insights import AgentKind, FinOpsInsight, insight_fingerprint


class FinOpsInsightService:
    def __init__(self, *, repository: Any, runner: Any) -> None:
        self._repository = repository
        self._runner = runner

    def analyze(
        self,
        *,
        agent_kind: AgentKind,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        window: Mapping[str, Any],
        trigger_type: str,
        trigger_ref: str | None,
        source_revision: str,
        input_payload: Mapping[str, Any],
    ) -> FinOpsInsight:
        previous = self.latest(
            tenant_ref=tenant_ref,
            authorized_workspace_ids=workspace_ids,
            agent_kind=agent_kind,
        )
        result = self._runner.analyze(
            agent_kind=agent_kind,
            tenant_ref=tenant_ref,
            workspace_ids=workspace_ids,
            window=window,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            source_revision=source_revision,
            input_payload=input_payload,
        )
        if (
            result.status == "failed"
            and previous is not None
            and previous.status == "ready"
            and previous.insight_id != result.insight_id
        ):
            self._repository.replace(
                previous.model_copy(update={"status": "stale"})
            )
        return result

    def fingerprint(
        self,
        *,
        agent_kind: AgentKind,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        trigger_type: str,
        trigger_ref: str | None,
        source_revision: str,
    ) -> str:
        return insight_fingerprint(
            tenant_ref=tenant_ref,
            workspace_ids=workspace_ids,
            agent_kind=agent_kind,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            source_revision=source_revision,
        )

    def by_fingerprint(
        self,
        *,
        agent_kind: AgentKind,
        tenant_ref: str,
        trigger_fingerprint: str,
    ) -> FinOpsInsight | None:
        return self._repository.get_by_fingerprint(
            tenant_ref=tenant_ref,
            agent_kind=agent_kind,
            trigger_fingerprint=trigger_fingerprint,
        )

    def list(
        self,
        *,
        tenant_ref: str,
        authorized_workspace_ids: tuple[str, ...],
        agent_kind: AgentKind | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> InsightPage:
        return self._repository.list(
            tenant_ref=tenant_ref,
            authorized_workspace_ids=authorized_workspace_ids,
            agent_kind=agent_kind,
            cursor=cursor,
            limit=limit,
        )

    def latest(
        self,
        *,
        tenant_ref: str,
        authorized_workspace_ids: tuple[str, ...],
        agent_kind: AgentKind,
    ) -> FinOpsInsight | None:
        page = self.list(
            tenant_ref=tenant_ref,
            authorized_workspace_ids=authorized_workspace_ids,
            agent_kind=agent_kind,
            limit=100,
        )
        for status in ("ready", "stale", "insufficient_data", "failed"):
            match = next((item for item in page.items if item.status == status), None)
            if match is not None:
                return match
        return None
