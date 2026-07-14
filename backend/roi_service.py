from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    from .identity import canonical_actor_identity, is_trusted_tenant_identity
except ImportError:
    from identity import canonical_actor_identity, is_trusted_tenant_identity


MAX_WINDOW_DAYS = 31
MAX_RECORDS_PER_KIND = 300
PRICE_UNIT = "per_1m_tokens"


class RoiWindowError(ValueError):
    pass


class PriceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    unit: Literal["per_1m_tokens"]
    effective_from: datetime
    effective_to: datetime | None = None
    input_per_1m: float
    output_per_1m: float

    @field_validator("currency")
    @classmethod
    def strict_currency(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency must be an uppercase ISO-4217 code")
        return value

    @field_validator("effective_from", "effective_to")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include UTC offset")
        return value.astimezone(timezone.utc)

    @field_validator("input_per_1m", "output_per_1m")
    @classmethod
    def finite_rate(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("rate must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def valid_interval(self) -> "PriceEntry":
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class PriceCatalog(BaseModel):
    prices: list[PriceEntry]

    @model_validator(mode="after")
    def no_overlaps(self) -> "PriceCatalog":
        by_model: dict[str, list[PriceEntry]] = {}
        for price in self.prices:
            by_model.setdefault(price.model, []).append(price)
        for rows in by_model.values():
            rows.sort(key=lambda item: item.effective_from)
            for previous, current in zip(rows, rows[1:]):
                if previous.effective_to is None or current.effective_from < previous.effective_to:
                    raise ValueError("overlapping price windows for model")
        return self


class CostSummary(BaseModel):
    total: float | None
    status: Literal["complete", "partial", "unknown"]
    currency: str | None
    by_currency: dict[str, float]
    unpriced_models: list[str] = Field(default_factory=list)

    @field_validator("total")
    @classmethod
    def finite_total(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("cost total must be finite and non-negative")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency must be an uppercase ISO-4217 code")
        return value

    @field_validator("by_currency")
    @classmethod
    def finite_currency_amounts(cls, value: dict[str, float]) -> dict[str, float]:
        for currency, amount in value.items():
            if not re.fullmatch(r"[A-Z]{3}", currency) or not math.isfinite(amount) or amount < 0:
                raise ValueError("currency amounts must be finite non-negative ISO-4217 values")
        return value

    @model_validator(mode="after")
    def consistent_status(self) -> "CostSummary":
        if self.status == "complete":
            if self.total is None or self.currency is None or self.by_currency != {self.currency: self.total}:
                raise ValueError("complete cost requires matching total, currency, and by_currency")
        elif self.status == "partial":
            if self.total is not None or self.currency is not None:
                raise ValueError("partial cost must not expose a single total or currency")
        elif self.total is not None or self.currency is not None or self.by_currency:
            raise ValueError("unknown cost must not expose priced amounts")
        return self


class UsageSummary(BaseModel):
    runs: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    models: list[str]
    duplicate_usage_event_ids: list[str] = Field(default_factory=list)


class Assumption(BaseModel):
    kind: str
    source: str
    formula: str
    status: str
    model: str | None = None
    version: str | None = None
    currency: str | None = None
    unit: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    input_per_1m: float | None = None
    output_per_1m: float | None = None


class TimeValueSummary(BaseModel):
    hours: float | None
    cash_value: float | None
    status: Literal["not_monetized", "measured"]

    @field_validator("hours", "cash_value")
    @classmethod
    def finite_nonnegative(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("time value must be finite and non-negative")
        return value


class BusinessValueSummary(BaseModel):
    total: float | None
    currency: str | None
    by_currency: dict[str, float]
    status: Literal["measured", "not_monetized", "partial"]

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency must be an uppercase ISO-4217 code")
        return value

    @field_validator("total")
    @classmethod
    def finite_total(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("business value must be finite and non-negative")
        return value

    @field_validator("by_currency")
    @classmethod
    def finite_currency_amounts(cls, value: dict[str, float]) -> dict[str, float]:
        return CostSummary.finite_currency_amounts(value)


class RoiSnapshot(BaseModel):
    workspace_id: str
    window: dict[str, str]
    generated_at: datetime
    status: Literal["estimated", "measured", "verified"]
    usage: UsageSummary
    cost: CostSummary
    time_value: TimeValueSummary
    business_value: BusinessValueSummary | None
    observed_run_ids: list[str]
    lineage_complete: bool
    invalid_run_ids: list[str]
    outcome_event_ids: list[str]
    verified_outcome_event_ids: list[str]
    unverified_outcome_event_ids: list[str]
    assumptions: list[Assumption]
    truncated: bool = False


class ChargebackGroup(BaseModel):
    member: dict[str, Any]
    currency: str | None
    model: str | None
    task_kind: str
    window: dict[str, str]
    activity_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost: CostSummary

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str | None) -> str | None:
        return CostSummary.valid_currency(value)


class ChargebackMember(BaseModel):
    member: dict[str, Any]
    groups: int = Field(ge=0)
    cost: CostSummary


class ChargebackSnapshot(BaseModel):
    workspace_id: str
    window: dict[str, str]
    groups: list[ChargebackGroup]
    members: list[ChargebackMember]
    totals: CostSummary
    duplicate_event_ids: list[str] = Field(default_factory=list)
    duplicate_event_count: int = Field(ge=0, default=0)
    truncated: bool = False
    record_limit_per_kind: int = MAX_RECORDS_PER_KIND


def parse_time_window(from_value: Any, to_value: Any, *, max_days: int = MAX_WINDOW_DAYS) -> dict[str, str]:
    start, end = _utc(from_value, "from"), _utc(to_value, "to")
    if end <= start:
        raise RoiWindowError("to must be after from")
    if end - start > timedelta(days=max_days):
        raise RoiWindowError(f"time window cannot exceed {max_days} days")
    return {"from": start.isoformat(), "to": end.isoformat()}


def record_in_window(record: Mapping[str, Any], window: Mapping[str, Any], kind: str = "run") -> bool:
    at = _record_time(record, kind)
    return at is not None and _utc(window["from"], "from") <= at < _utc(window["to"], "to")


def load_price_catalog() -> PriceCatalog:
    raw = str(os.environ.get("DF_ROI_PRICE_CONFIG_JSON") or "").strip()
    if not raw:
        file_name = str(os.environ.get("DF_ROI_PRICE_CONFIG_FILE") or "").strip()
        if not file_name:
            return PriceCatalog(prices=[])
        raw = Path(file_name).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("ROI price configuration must be JSON") from exc
    return PriceCatalog.model_validate(payload if isinstance(payload, Mapping) else {"prices": payload})


def build_roi_snapshot(workspace_id: str, window: Mapping[str, Any], *, runs: Iterable[Mapping[str, Any]], outcomes: Iterable[Mapping[str, Any]], prices: Iterable[Mapping[str, Any]] | None = None, source_validator: Callable[[str, Mapping[str, Any]], bool] | None = None, verification_events: Iterable[Mapping[str, Any]] = (), truncated: bool = False) -> dict[str, Any]:
    normalized_window = parse_time_window(window.get("from"), window.get("to"))
    catalog = _catalog(prices)
    run_items, was_truncated = _filtered(workspace_id, runs, normalized_window, "run")
    usage, cost, assumptions = _usage_cost(run_items, catalog)
    evidence = [item for item in outcomes if _valid_outcome(workspace_id, item, normalized_window, source_validator)]
    verification_by_id = {str(item.get("event_id")): item for item in verification_events if isinstance(item, Mapping) and item.get("event_id")}
    verified = [item for item in evidence if _verified(workspace_id, item, verification_by_id)]
    unverified = [str(item["event_id"]) for item in evidence if not _verified(workspace_id, item, verification_by_id)]
    status: Literal["estimated", "measured", "verified"] = "estimated"
    if evidence:
        status = "verified" if not unverified else "measured"
    business_value, business_assumptions = _business_value(evidence)
    observed_run_ids, invalid_run_ids = _run_lineage(run_items)
    was_incomplete = bool(truncated or was_truncated or invalid_run_ids)
    model = RoiSnapshot(workspace_id=str(workspace_id), window=normalized_window, generated_at=datetime.now(timezone.utc), status=status, usage=usage, cost=cost, time_value=TimeValueSummary(hours=None, cash_value=None, status="not_monetized"), business_value=business_value, observed_run_ids=observed_run_ids, lineage_complete=not was_incomplete, invalid_run_ids=invalid_run_ids, outcome_event_ids=[str(item["event_id"]) for item in evidence if item.get("event_id")], verified_outcome_event_ids=[str(item["event_id"]) for item in verified if item.get("event_id")], unverified_outcome_event_ids=unverified, assumptions=[*assumptions, Assumption(kind="time_value", source="not_configured", formula="saved time is not cash", status="not_monetized"), *business_assumptions], truncated=truncated or was_truncated)
    return model.model_dump(mode="json")


def member_chargeback(workspace_id: str, window: Mapping[str, Any], *, runs: Iterable[Mapping[str, Any]], messages: Iterable[Mapping[str, Any]], tasks: Iterable[Mapping[str, Any]], memberships: Iterable[Mapping[str, Any]], prices: Iterable[Mapping[str, Any]] | None = None, pseudonym_salt: str | None = None, truncated: bool = False) -> dict[str, Any]:
    normalized_window, catalog = parse_time_window(window.get("from"), window.get("to")), _catalog(prices)
    salt = pseudonym_salt or os.environ.get("DF_ROI_PSEUDONYM_SALT") or os.environ.get("DF_WEB_PROXY_SECRET")
    if not salt:
        raise PermissionError("ROI pseudonym salt is not configured")
    members = _memberships(memberships)
    groups: dict[tuple[str, str | None, str | None, str], dict[str, Any]] = {}
    seen: set[str] = set()
    duplicate_event_ids: list[str] = []
    truncation = truncated
    for kind, records in (("run", runs), ("message", messages), ("task", tasks)):
        filtered, was_truncated = _filtered(workspace_id, records, normalized_window, kind)
        truncation = truncation or was_truncated
        for record in filtered:
            actor_key = _trusted_actor(record)
            if not actor_key:
                continue
            member = _member_label(workspace_id, actor_key, members, salt)
            if kind != "run":
                stable_id = str(record.get("message_id") if kind == "message" else record.get("task_id") or "").strip()
                if not stable_id:
                    continue
                event_id = f"{kind}:{stable_id}"
                if event_id in seen:
                    duplicate_event_ids.append(event_id)
                    continue
                seen.add(event_id)
                key = (actor_key, None, None, str(record.get("task_type") or kind))
                row = groups.setdefault(key, _group(member, None, None, key[3], normalized_window))
                row["activity_count"] += 1
                continue
            for index, model_name, tokens in _run_usage(record):
                event_id = str(record.get("run_id") or "run") + ":" + str(record.get("models", [])[index].get("usage_event_id") or record.get("models", [])[index].get("response_id") or index)
                if event_id in seen:
                    duplicate_event_ids.append(event_id)
                    continue
                seen.add(event_id)
                price = _price(catalog, model_name, _record_time(record, "run"))
                currency = price.currency if price else None
                key = (actor_key, currency, model_name, "run")
                row = groups.setdefault(key, _group(member, currency, model_name, "run", normalized_window))
                row["activity_count"] += 1
                if tokens is None or price is None:
                    row["cost"]["status"] = "partial"
                    continue
                amount = tokens["input_tokens"] / 1_000_000 * price.input_per_1m + tokens["output_tokens"] / 1_000_000 * price.output_per_1m
                row["input_tokens"] += tokens["input_tokens"]; row["output_tokens"] += tokens["output_tokens"]; row["total_tokens"] += tokens["total_tokens"]; row["cost"]["total"] += amount
    group_rows = []
    totals: dict[str, float] = {}
    any_partial = False
    for row in groups.values():
        if row["cost"]["currency"] is None:
            row["cost"] = {"total": None, "status": "partial" if row["model"] else "unknown", "currency": None, "by_currency": {}}
            any_partial = any_partial or bool(row["model"])
        elif row["cost"]["status"] == "partial":
            row["cost"] = {"total": None, "status": "partial", "currency": None, "by_currency": {}}
            any_partial = True
        else:
            row["cost"]["total"] = _money(row["cost"]["total"])
            row["cost"]["by_currency"] = {row["cost"]["currency"]: row["cost"]["total"]}
            totals[row["cost"]["currency"]] = totals.get(row["cost"]["currency"], 0) + row["cost"]["total"]
        group_rows.append(row)
    total = _money(next(iter(totals.values()))) if len(totals) == 1 and not any_partial else None
    summary: dict[str, dict[str, Any]] = {}
    for row in group_rows:
        key = row["member"]["actor_id"]
        member_summary = summary.setdefault(key, {"member": row["member"], "groups": 0, "cost_by_currency": {}, "partial": False, "has_priced": False})
        member_summary["groups"] += 1
        if row["cost"]["status"] == "partial":
            member_summary["partial"] = True
        currency = row["cost"].get("currency")
        if currency and row["cost"]["status"] == "complete":
            member_summary["has_priced"] = True
            member_summary["cost_by_currency"][currency] = member_summary["cost_by_currency"].get(currency, 0) + row["cost"]["total"]
    members_out = []
    for item in summary.values():
        by_currency = {currency: _money(amount) for currency, amount in item.pop("cost_by_currency").items()}
        partial = item.pop("partial")
        has_priced = item.pop("has_priced")
        total_cost = _money(next(iter(by_currency.values()))) if len(by_currency) == 1 and not partial else None
        status = "complete" if total_cost is not None else "partial" if partial or has_priced else "unknown"
        item["cost"] = CostSummary(total=total_cost, status=status, currency=next(iter(by_currency)) if status == "complete" else None, by_currency=by_currency if status != "unknown" else {})
        members_out.append(item)
    total_status = "complete" if total is not None else "partial" if totals or any_partial else "unknown"
    return ChargebackSnapshot(workspace_id=str(workspace_id), window=normalized_window, groups=sorted(group_rows, key=lambda item: str(item["member"]["actor_id"])), members=members_out, totals=CostSummary(total=total, status=total_status, currency=next(iter(totals)) if total_status == "complete" else None, by_currency={key: _money(value) for key, value in totals.items()} if total_status != "unknown" else {}), duplicate_event_ids=duplicate_event_ids, duplicate_event_count=len(duplicate_event_ids), truncated=truncation).model_dump(mode="json")


def _catalog(prices: Iterable[Mapping[str, Any]] | None) -> PriceCatalog:
    return load_price_catalog() if prices is None else PriceCatalog.model_validate({"prices": list(prices)})


def _usage_cost(runs: list[Mapping[str, Any]], catalog: PriceCatalog) -> tuple[UsageSummary, CostSummary, list[Assumption]]:
    inputs = outputs = totals = 0; observed = False; unpriced: set[str] = set(); by_currency: dict[str, float] = {}; assumptions: list[Assumption] = []; seen: set[str] = set(); duplicates: list[str] = []
    for run in runs:
        for index, model, tokens in _run_usage(run):
            event_id = str(run.get("run_id") or "run") + ":" + str(run.get("models", [])[index].get("usage_event_id") or run.get("models", [])[index].get("response_id") or index)
            if event_id in seen: duplicates.append(event_id); continue
            seen.add(event_id); observed = True
            if tokens is None: unpriced.add(model); continue
            inputs += tokens["input_tokens"]; outputs += tokens["output_tokens"]; totals += tokens["total_tokens"]
            price = _price(catalog, model, _record_time(run, "run"))
            if price is None: unpriced.add(model); continue
            by_currency[price.currency] = by_currency.get(price.currency, 0) + tokens["input_tokens"] / 1_000_000 * price.input_per_1m + tokens["output_tokens"] / 1_000_000 * price.output_per_1m
            assumptions.append(Assumption(kind="model_price", source=price.source, formula="input_tokens/1_000_000*input_per_1m + output_tokens/1_000_000*output_per_1m", status="configured", model=price.model, version=price.version, currency=price.currency, unit=price.unit, effective_from=price.effective_from, effective_to=price.effective_to, input_per_1m=price.input_per_1m, output_per_1m=price.output_per_1m))
    status = "unknown" if not observed else "partial" if unpriced or len(by_currency) != 1 else "complete"
    total = _money(next(iter(by_currency.values()))) if status == "complete" else None
    return UsageSummary(runs=len(runs), input_tokens=inputs if observed else None, output_tokens=outputs if observed else None, total_tokens=totals if observed else None, models=sorted({model for run in runs for _, model, _ in _run_usage(run)}), duplicate_usage_event_ids=duplicates), CostSummary(total=total, status=status, currency=next(iter(by_currency)) if status == "complete" else None, by_currency={key: _money(value) for key, value in by_currency.items()} if status != "unknown" else {}, unpriced_models=sorted(unpriced)), _dedupe(assumptions)


def _run_lineage(runs: Iterable[Mapping[str, Any]]) -> tuple[list[str], list[str]]:
    valid: set[str] = set()
    invalid: set[str] = set()
    for run in runs:
        identifier = str(run.get("run_id") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", identifier):
            valid.add(identifier)
        else:
            invalid.add("sha256:" + sha256(identifier.encode("utf-8")).hexdigest())
    return sorted(valid), sorted(invalid)


def _run_usage(run: Mapping[str, Any]) -> list[tuple[int, str, dict[str, int] | None]]:
    output = []
    for index, item in enumerate(run.get("models") or []):
        if not isinstance(item, Mapping): continue
        model = str(item.get("model") or item.get("model_name") or "unknown_model")
        usage = item.get("usage")
        if not isinstance(usage, Mapping): output.append((index, model, None)); continue
        if "input_tokens" not in usage or "output_tokens" not in usage:
            if "total_tokens" in usage or "total" in usage: output.append((index, model, None)); continue
        output.append((index, model, _tokens(usage)))
    return output


def _tokens(usage: Mapping[str, Any]) -> dict[str, int]:
    values = {"input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")), "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")), "total_tokens": usage.get("total_tokens", usage.get("total"))}
    result = {}
    for key, value in values.items():
        if value is None and key == "total_tokens": continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be finite and non-negative")
        result[key] = int(value)
    result["total_tokens"] = int(result.get("total_tokens", result["input_tokens"] + result["output_tokens"]))
    return result


def _valid_outcome(workspace: str, item: Mapping[str, Any], window: Mapping[str, str], validator: Callable[[str, Mapping[str, Any]], bool] | None) -> bool:
    return bool(item.get("workspace_id") == workspace and item.get("provenance") == "observed" and item.get("observed_value") is not None and record_in_window({"completed_at": item.get("observed_at")}, window) and isinstance(item.get("source"), Mapping) and validator is not None and validator(workspace, item["source"]))


def _verified(workspace_id: str, item: Mapping[str, Any], verification_by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    verification = item.get("verification") if isinstance(item.get("verification"), Mapping) else {}
    verification_id = str(verification.get("verification_event_id") or "").strip()
    event = verification_by_id.get(verification_id) if verification_id else None
    reviewer = verification.get("reviewer") if isinstance(verification.get("reviewer"), Mapping) else {}
    outcome_actor = item.get("actor") if isinstance(item.get("actor"), Mapping) else {}
    if not event or not item.get("trusted_identity") or not is_trusted_tenant_identity(outcome_actor) or verification.get("status") != "verified":
        return False
    event_actor = event.get("actor") if isinstance(event.get("actor"), Mapping) else {}
    return bool(
        event.get("workspace_id") == workspace_id
        and event.get("kind") == "outcome_verification"
        and event.get("outcome_event_id") == item.get("event_id")
        and event.get("trusted_identity")
        and is_trusted_tenant_identity(event_actor)
        and canonical_actor_identity(event_actor)
        and canonical_actor_identity(event_actor) == canonical_actor_identity(reviewer)
        and canonical_actor_identity(event_actor) != canonical_actor_identity(outcome_actor)
    )


def _business_value(outcomes: list[Mapping[str, Any]]) -> tuple[BusinessValueSummary | None, list[Assumption]]:
    if not outcomes: return None, []
    values: dict[str, float] = {}; assumptions = []
    for item in outcomes:
        value = item.get("business_value") if isinstance(item.get("business_value"), Mapping) else None
        if not value: continue
        amount = value.get("value"); currency = str(value.get("currency") or "")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount < 0: raise ValueError("business_value must be finite and non-negative")
        if not re.fullmatch(r"[A-Z]{3}", currency): raise ValueError("business_value currency must be an uppercase ISO-4217 code")
        values[currency] = values.get(currency, 0) + amount; assumptions.append(Assumption(kind="business_value", source=str(value["source"]), formula=str(value["formula"]), status=str(value["status"]), currency=currency))
    return (BusinessValueSummary(total=_money(next(iter(values.values()))) if len(values) == 1 else None, currency=next(iter(values)) if len(values) == 1 else None, by_currency={key: _money(value) for key, value in values.items()}, status="measured" if values else "not_monetized"), assumptions)


def _filtered(workspace: str, records: Iterable[Mapping[str, Any]], window: Mapping[str, str], kind: str) -> tuple[list[Mapping[str, Any]], bool]:
    selected = [item for item in records if isinstance(item, Mapping) and item.get("workspace_id") == workspace and record_in_window(item, window, kind)]
    return selected[:MAX_RECORDS_PER_KIND], len(selected) > MAX_RECORDS_PER_KIND


def _trusted_actor(record: Mapping[str, Any]) -> str | None:
    actor = record.get("actor") if isinstance(record.get("actor"), Mapping) else {}
    identity = canonical_actor_identity(actor)
    return f"{identity[0]}:{identity[1]}" if record.get("trusted_identity") and is_trusted_tenant_identity(actor) and identity else None


def _memberships(items: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping) or str(item.get("status") or "").lower() != "active":
            continue
        identity = canonical_actor_identity(item)
        if identity:
            result[f"{identity[0]}:{identity[1]}"] = dict(item)
    return result


def _member_label(workspace: str, actor_key: str, members: Mapping[str, Mapping[str, Any]], salt: str) -> dict[str, Any]:
    subject_label = "member_" + hmac.new(salt.encode(), f"{workspace}:member:{actor_key}".encode(), hashlib.sha256).hexdigest()[:40]
    item = members.get(actor_key)
    if item: return {"actor_id": str(item.get("actor_id")), "email": item.get("email"), "name": item.get("name") or item.get("user"), "status": "active", "subject_label": subject_label}
    pseudo = hmac.new(salt.encode(), f"{workspace}:{actor_key}".encode(), hashlib.sha256).hexdigest()[:20]
    return {"actor_id": f"actor_{pseudo}", "email": None, "name": None, "status": "unknown_or_departed", "subject_label": subject_label}


def _group(member: Mapping[str, Any], currency: str | None, model: str | None, task_kind: str, window: Mapping[str, str]) -> dict[str, Any]:
    return {"member": dict(member), "currency": currency, "model": model, "task_kind": task_kind, "window": dict(window), "activity_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": {"total": 0.0, "status": "complete", "currency": currency, "by_currency": {}}}


def _price(catalog: PriceCatalog, model: str, at: datetime | None) -> PriceEntry | None:
    if at is None: return None
    return next((entry for entry in catalog.prices if entry.model == model and entry.effective_from <= at and (entry.effective_to is None or at < entry.effective_to)), None)


def _record_time(record: Mapping[str, Any], kind: str) -> datetime | None:
    fields = {"run": ("completed_at", "updated_at", "started_at", "time"), "message": ("time", "created_at", "updated_at"), "task": ("created_at", "updated_at", "time")}[kind]
    for field in fields:
        try: return _utc(record.get(field), field)
        except RoiWindowError: continue
    return None


def _utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value: raise RoiWindowError(f"{name} must be an ISO-8601 UTC timestamp")
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise RoiWindowError(f"{name} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise RoiWindowError(f"{name} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _dedupe(items: list[Assumption]) -> list[Assumption]:
    result = []; seen = set()
    for item in items:
        key = item.model_dump_json()
        if key not in seen: seen.add(key); result.append(item)
    return result


def _money(value: float) -> float: return round(value, 6)


__all__ = ["ChargebackSnapshot", "PriceCatalog", "PriceEntry", "RoiSnapshot", "RoiWindowError", "build_roi_snapshot", "load_price_catalog", "member_chargeback", "parse_time_window", "record_in_window"]
