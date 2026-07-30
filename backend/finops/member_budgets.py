from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemberBudgetDraft(BaseModel):
    """UTC calendar-month USD budget selected for one opaque member reference."""

    model_config = ConfigDict(extra="forbid")

    member_ref: str = Field(min_length=8, max_length=128)
    amount_usd: Decimal = Field(gt=0, max_digits=19, decimal_places=8)
    thresholds_pct: tuple[int, ...] = (80, 95, 100)
    enabled: bool = True

    @field_validator("thresholds_pct")
    @classmethod
    def validate_thresholds(cls, value: tuple[int, ...] | list[int]) -> tuple[int, ...]:
        normalized = tuple(int(item) for item in value)
        if (
            not normalized
            or normalized != tuple(sorted(set(normalized)))
            or any(item < 1 or item > 100 for item in normalized)
        ):
            raise ValueError("thresholds must be unique ascending integers from 1 to 100")
        return normalized


class MemberBudget(MemberBudgetDraft):
    budget_id: str = Field(min_length=1, max_length=64)
    period_type: Literal["calendar_month_utc"] = "calendar_month_utc"
    revision: int = Field(ge=1)
    created_by_ref: str = Field(min_length=1, max_length=128)
    updated_by_ref: str = Field(min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime


class MemberCostSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_ref: str = Field(min_length=1, max_length=128)
    estimated_spend_usd: Decimal | None
    priced_requests: int = Field(ge=0)
    total_requests: int = Field(ge=0)
    pricing_coverage_pct: float | None = Field(default=None, ge=0, le=100)
    primary_model: str | None = Field(default=None, max_length=160)
    data_status: Literal["complete", "partial", "unavailable"] | None = None

    @model_validator(mode="after")
    def derive_pricing_coverage(self) -> "MemberCostSummary":
        if self.priced_requests > self.total_requests:
            raise ValueError("priced_requests cannot exceed total_requests")
        coverage = (
            round(self.priced_requests / self.total_requests * 100, 4)
            if self.total_requests
            else None
        )
        status: Literal["complete", "partial", "unavailable"]
        if coverage is None or self.priced_requests == 0 or self.estimated_spend_usd is None:
            status = "unavailable"
        elif self.priced_requests == self.total_requests:
            status = "complete"
        else:
            status = "partial"
        if self.pricing_coverage_pct is not None and self.pricing_coverage_pct != coverage:
            raise ValueError("pricing_coverage_pct must match request coverage")
        if self.data_status is not None and self.data_status != status:
            raise ValueError("data_status must match pricing coverage")
        self.pricing_coverage_pct = coverage
        self.data_status = status
        return self


class BudgetAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(min_length=1, max_length=64)
    tenant_ref: str = Field(min_length=1, max_length=128, exclude=True, repr=False)
    budget_id: str = Field(min_length=1, max_length=64)
    actor_ref: str = Field(min_length=1, max_length=128)
    period_key: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    threshold_pct: int = Field(ge=1, le=100)
    budget_amount_usd: Decimal = Field(gt=0, max_digits=19, decimal_places=8)
    estimated_spend_usd: Decimal = Field(ge=0, max_digits=19, decimal_places=8)
    pricing_coverage_pct: float | None = Field(default=None, ge=0, le=100)
    budget_revision: int = Field(ge=1)
    notification_revision: int = Field(ge=1)
    delivery_state: Literal["pending", "sending", "sent", "failed", "suppressed"]
    safe_error_category: str | None = Field(default=None, max_length=64)
    attempt_count: int = Field(default=0, ge=0, le=3)
    triggered_at: datetime
    sent_at: datetime | None = None
    updated_at: datetime
    lease_token: str | None = Field(
        default=None, min_length=8, max_length=64, repr=False, exclude=True
    )
    lease_expires_at: datetime | None = Field(default=None, exclude=True)
    next_attempt_at: datetime | None = Field(default=None, exclude=True)


class NotificationSetting(BaseModel):
    """One revisioned, tenant-scoped administrator recipient configuration."""

    model_config = ConfigDict(extra="forbid")

    recipient_actor_ref: str = Field(min_length=8, max_length=128)
    recipient_email: str = Field(min_length=3, max_length=320, repr=False)
    sender_display_name: str = Field(min_length=1, max_length=120)
    subject_template: str = Field(min_length=1, max_length=200)
    body_template: str = Field(min_length=1, max_length=4000)
    enabled: bool
    test_email_succeeded_at: datetime | None = None
    revision: int = Field(ge=1)
    created_by_ref: str = Field(min_length=1, max_length=128)
    updated_by_ref: str = Field(min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime


__all__ = [
    "BudgetAlert",
    "MemberBudget",
    "MemberBudgetDraft",
    "MemberCostSummary",
    "NotificationSetting",
]
