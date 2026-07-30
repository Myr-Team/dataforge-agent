from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BudgetSubject(BaseModel):
    """Workspace-scoped display identity used by budget and cost projections."""

    model_config = ConfigDict(extra="forbid")

    subject_ref: str = Field(min_length=8, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=120)
    department_label: str | None = Field(default=None, max_length=120)
    primary_model: str | None = Field(default=None, max_length=160)
    enabled: bool = True
    revision: int = Field(ge=1)
    updated_at: datetime


def budget_subject_ref(*, workspace_id: str, display_name: str, secret: str) -> str:
    clean_workspace = str(workspace_id or "").strip()
    clean_name = " ".join(str(display_name or "").split())
    clean_secret = str(secret or "").strip()
    if not clean_workspace or not clean_name or not clean_secret:
        raise ValueError("workspace_id, display_name and secret are required")
    digest = hmac.new(
        clean_secret.encode("utf-8"),
        f"{clean_workspace.casefold()}:{clean_name.casefold()}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"subject_{digest[:40]}"


__all__ = ["BudgetSubject", "budget_subject_ref"]
