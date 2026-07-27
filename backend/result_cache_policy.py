from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .finops.models import ResultCacheEvidence


_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class GenerationParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_output_tokens: int | None = Field(default=None, ge=1, le=384_000)
    seed: int | None = None
    response_schema_revision: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )


class ResultCacheContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_ref: str = Field(min_length=1, max_length=160)
    workspace_id: str = Field(min_length=1, max_length=160)
    data_revision: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,239}$",
    )
    execution_kind: str = Field(min_length=1, max_length=80)
    agent_id: str = Field(min_length=1, max_length=160)
    provider_type: Literal["azure_foundry", "deepseek"]
    provider_id: str | None = Field(default=None, max_length=80)
    model_id: str = Field(min_length=1, max_length=160)
    route_revision: int = Field(ge=0)
    prompt_revision: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    tool_schema_revision: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    generation_parameters: GenerationParameters = Field(
        default_factory=GenerationParameters
    )
    policy_revision: int = Field(ge=0)
    enabled: bool = True
    live_data: bool = False
    side_effecting_tools: bool = False
    conversation_stable: bool = True


class ResultCacheDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_key: str | None
    evidence: ResultCacheEvidence


def evaluate_result_cache(context: ResultCacheContext) -> ResultCacheDecision:
    reason = _bypass_reason(context)
    if reason is not None:
        return ResultCacheDecision(
            cache_key=None,
            evidence=ResultCacheEvidence(
                eligible=False,
                state="bypassed",
                reason=reason,
                policy_revision=context.policy_revision,
            ),
        )
    material = context.model_dump(
        mode="json",
        exclude={
            "enabled",
            "live_data",
            "side_effecting_tools",
            "conversation_stable",
        },
    )
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return ResultCacheDecision(
        cache_key=f"dataforge:result:v2:{digest}",
        evidence=ResultCacheEvidence(
            eligible=True,
            state="miss",
            reason="eligible",
            policy_revision=context.policy_revision,
        ),
    )


def _bypass_reason(
    context: ResultCacheContext,
) -> Literal[
    "disabled",
    "live_data",
    "side_effecting_tools",
    "unstable_conversation",
    "data_revision_missing",
] | None:
    if not context.enabled:
        return "disabled"
    if context.live_data:
        return "live_data"
    if context.side_effecting_tools:
        return "side_effecting_tools"
    if not context.conversation_stable:
        return "unstable_conversation"
    if not context.data_revision:
        return "data_revision_missing"
    return None


__all__ = [
    "GenerationParameters",
    "ResultCacheContext",
    "ResultCacheDecision",
    "evaluate_result_cache",
]
