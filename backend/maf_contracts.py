"""Typed contracts and runtime configuration for the MAF execution path."""

from __future__ import annotations

import hashlib
import os
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MafRuntimeMode(str, Enum):
    OFF = "off"
    AUDIT = "audit"
    FULL = "full"


class CollaborationPattern(str, Enum):
    DIRECT = "direct"
    CONCURRENT_RESEARCH = "concurrent_research"
    SPECIALIST_HANDOFF = "specialist_handoff"
    BOUNDED_REVIEW = "bounded_review"


class MafAgentRecord(BaseModel):
    agent_id: str
    role: str
    status: str = "pending"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CollaborationPlan(BaseModel):
    pattern: CollaborationPattern
    agents: list[MafAgentRecord] = Field(default_factory=list)
    max_revisions: int = Field(default=0, ge=0)


class MafRunSummary(BaseModel):
    run_id: str
    runtime_mode: MafRuntimeMode
    collaboration: CollaborationPlan
    status: str = "pending"
    revisions: int = Field(default=0, ge=0)
    agents: list[MafAgentRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def runtime_mode() -> MafRuntimeMode:
    """Resolve explicit MAF runtime configuration before the legacy flag."""
    configured = os.environ.get("DF_MAF_RUNTIME")
    if configured is not None and configured.strip():
        return MafRuntimeMode(configured.strip().lower())
    if os.environ.get("DF_USE_MAF") == "1":
        return MafRuntimeMode.AUDIT
    return MafRuntimeMode.OFF


def traffic_percent() -> int:
    """Return the configured canary percentage, clamped to 0..100."""
    try:
        value = int(os.environ.get("DF_MAF_TRAFFIC_PERCENT", "0"))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))


def canary_selected(workspace_id: str, conversation_id: str) -> bool:
    digest = hashlib.sha256(f"{workspace_id}:{conversation_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < traffic_percent()
