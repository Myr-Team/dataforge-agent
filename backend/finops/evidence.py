from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EvidenceObjectKind = Literal["request", "run", "trace", "apim", "price_revision"]
_CHINA_TIME = timezone(timedelta(hours=8))
OPERATION_LABELS = {
    "analysis_run": "分析运行",
    "conversation_followup": "会话跟进",
    "model_call": "模型调用",
    "tool_call": "工具调用",
    "cache_evaluation": "缓存评估",
    "outcome_verification": "结果验证",
    "governance_action": "治理动作",
}


class FinOpsEvidenceAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_ref: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=160)
    object_kind: EvidenceObjectKind
    object_ref: str = Field(min_length=1, max_length=256)
    operation_code: str = Field(min_length=1, max_length=64)
    workspace_name_snapshot: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=320)
    occurred_at: datetime
    created_at: datetime


def build_evidence_alias(
    *,
    tenant_ref: str,
    workspace_id: str,
    workspace_name: str,
    object_kind: EvidenceObjectKind,
    object_ref: str,
    operation_code: str,
    occurred_at: datetime,
    created_at: datetime | None = None,
) -> FinOpsEvidenceAlias:
    clean_workspace_name = " ".join(str(workspace_name or "").split()).strip()[:200]
    workspace_label = clean_workspace_name or "工作区"
    normalized_operation = (
        str(operation_code or "").strip()
        if str(operation_code or "").strip() in OPERATION_LABELS
        else "unknown"
    )
    operation_label = OPERATION_LABELS.get(normalized_operation, "操作记录")
    local_time = _aware_utc(occurred_at).astimezone(_CHINA_TIME)
    display_time = f"{local_time.month}月{local_time.day}日 {local_time:%H:%M}"
    return FinOpsEvidenceAlias(
        tenant_ref=tenant_ref,
        workspace_id=workspace_id,
        object_kind=object_kind,
        object_ref=object_ref,
        operation_code=normalized_operation,
        workspace_name_snapshot=workspace_label,
        display_name=f"{workspace_label} · {operation_label} · {display_time}",
        occurred_at=_aware_utc(occurred_at),
        created_at=_aware_utc(created_at or datetime.now(timezone.utc)),
    )


def operation_label(operation_code: str) -> str:
    return OPERATION_LABELS.get(str(operation_code or "").strip(), "操作记录")


def _aware_utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
