from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


MAX_WINDOW_DAYS = 31
MAX_RECORDS_PER_KIND = 300
PRICE_UNIT = "per_1m_tokens"


class RoiWindowError(ValueError):
    pass


def parse_time_window(from_value: Any, to_value: Any, *, max_days: int = MAX_WINDOW_DAYS) -> dict[str, str]:
    start = _parse_utc(from_value, "from")
    end = _parse_utc(to_value, "to")
    if end <= start:
        raise RoiWindowError("to must be after from")
    if end - start > timedelta(days=max_days):
        raise RoiWindowError(f"time window cannot exceed {max_days} days")
    return {"from": start.isoformat(), "to": end.isoformat()}


def load_price_catalog() -> list[dict[str, Any]]:
    raw = str(os.environ.get("DF_ROI_PRICE_CONFIG_JSON") or "").strip()
    source = "DF_ROI_PRICE_CONFIG_JSON"
    if not raw:
        path_value = str(os.environ.get("DF_ROI_PRICE_CONFIG_FILE") or "").strip()
        if not path_value:
            return []
        path = Path(path_value).expanduser()
        raw = path.read_text(encoding="utf-8")
        source = f"file:{path.name}"
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("ROI price configuration must be JSON") from exc
    rows = decoded.get("prices") if isinstance(decoded, Mapping) else decoded
    if not isinstance(rows, list):
        raise ValueError("ROI price configuration must contain a prices list")
    return [_normalize_price(row, source) for row in rows if isinstance(row, Mapping)]


def build_roi_snapshot(
    workspace_id: str,
    window: Mapping[str, Any],
    *,
    runs: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    prices: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_window = parse_time_window(window.get("from"), window.get("to"))
    catalog = _catalog(prices)
    run_items = _records_in_window(workspace_id, runs, normalized_window, kind="run")
    usage = _usage_and_cost(run_items, catalog)
    evidence = [
        item
        for item in outcomes
        if (not item.get("workspace_id") or str(item.get("workspace_id")) == str(workspace_id))
        and _source_linked_observed_outcome(item, normalized_window)
    ]
    verified = [item for item in evidence if _independently_verified(item)]
    status = "verified" if verified else "measured" if evidence else "estimated"
    business_value, business_assumptions = _business_value(evidence)
    time_assumption = {
        "kind": "time_value",
        "source": "not_configured",
        "formula": "not monetized; saved time is not treated as cash value",
        "status": "not_monetized",
    }
    return {
        "workspace_id": str(workspace_id),
        "window": normalized_window,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "usage": usage["usage"],
        "cost": usage["cost"],
        "time_value": {"hours": None, "cash_value": None, "status": "not_monetized"},
        "business_value": business_value,
        "outcome_event_ids": [str(item.get("event_id")) for item in evidence if item.get("event_id")],
        "verified_outcome_event_ids": [str(item.get("event_id")) for item in verified if item.get("event_id")],
        "assumptions": [*usage["assumptions"], time_assumption, *business_assumptions],
    }


def member_chargeback(
    workspace_id: str,
    window: Mapping[str, Any],
    *,
    runs: Iterable[Mapping[str, Any]],
    messages: Iterable[Mapping[str, Any]],
    tasks: Iterable[Mapping[str, Any]],
    memberships: Iterable[Mapping[str, Any]],
    prices: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_window = parse_time_window(window.get("from"), window.get("to"))
    catalog = _catalog(prices)
    member_index = _membership_index(memberships)
    rows: dict[str, dict[str, Any]] = {}

    for run in _records_in_window(workspace_id, runs, normalized_window, kind="run"):
        row = _actor_row(rows, _trusted_actor_id(run.get("actor")), member_index)
        if row is None:
            continue
        row["runs"] += 1
        usage = _usage_and_cost([run], catalog)
        row["input_tokens"] += usage["usage"]["input_tokens"] or 0
        row["output_tokens"] += usage["usage"]["output_tokens"] or 0
        row["total_tokens"] += usage["usage"]["total_tokens"] or 0
        if usage["cost"]["total"] is None and usage["cost"]["status"] == "partial":
            row["cost_status"] = "partial"
        elif usage["cost"]["total"] is not None:
            row["cost_total"] += usage["cost"]["total"]
            if row["cost_status"] == "unknown":
                row["cost_status"] = "complete"
        for model in usage["usage"]["models"]:
            row["models"][model] = row["models"].get(model, 0) + 1

    for message in _records_in_window(workspace_id, messages, normalized_window, kind="message"):
        row = _actor_row(rows, _trusted_actor_id(message.get("actor")), member_index)
        if row is not None:
            row["messages"] += 1
    for task in _records_in_window(workspace_id, tasks, normalized_window, kind="task"):
        row = _actor_row(rows, _trusted_actor_id(task.get("actor")), member_index)
        if row is not None:
            row["tasks"] += 1

    members = []
    for row in rows.values():
        cost_total = _money(row["cost_total"]) if row["cost_status"] == "complete" else None
        members.append(
            {
                "member": row["member"],
                "runs": row["runs"],
                "messages": row["messages"],
                "tasks": row["tasks"],
                "input_tokens": row["input_tokens"] or None,
                "output_tokens": row["output_tokens"] or None,
                "total_tokens": row["total_tokens"] or None,
                "models": sorted(row["models"]),
                "cost": {"total": cost_total, "status": row["cost_status"], "currency": "USD" if cost_total is not None else None},
            }
        )
    members.sort(key=lambda item: (item["total_tokens"] or 0, item["runs"] + item["messages"] + item["tasks"]), reverse=True)
    return {
        "workspace_id": str(workspace_id),
        "window": normalized_window,
        "basis": "trusted_actor_id_from_run_message_task",
        "members": members,
        "record_limit_per_kind": MAX_RECORDS_PER_KIND,
    }


def _catalog(prices: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if prices is None:
        return load_price_catalog()
    return [_normalize_price(item, "injected") for item in prices if isinstance(item, Mapping)]


def _normalize_price(item: Mapping[str, Any], default_source: str) -> dict[str, Any]:
    model = str(item.get("model") or "").strip()
    currency = str(item.get("currency") or "").strip().upper()
    unit = str(item.get("unit") or "").strip()
    effective_from = _parse_utc(item.get("effective_from"), "effective_from")
    effective_to_raw = item.get("effective_to")
    effective_to = _parse_utc(effective_to_raw, "effective_to") if effective_to_raw else None
    if not model or not currency or unit != PRICE_UNIT or effective_to is not None and effective_to <= effective_from:
        raise ValueError("invalid versioned ROI price configuration")
    return {
        "version": str(item.get("version") or "").strip() or "unversioned",
        "model": model,
        "currency": currency,
        "unit": unit,
        "input_per_1m": _nonnegative_number(item.get("input_per_1m"), "input_per_1m"),
        "output_per_1m": _nonnegative_number(item.get("output_per_1m"), "output_per_1m"),
        "effective_from": effective_from,
        "effective_to": effective_to,
        "source": str(item.get("source") or default_source),
    }


def _usage_and_cost(runs: Iterable[Mapping[str, Any]], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    run_items = list(runs)
    input_tokens = output_tokens = total_tokens = 0
    observed_usage = False
    unpriced_models: set[str] = set()
    currencies: set[str] = set()
    cost_total = 0.0
    models: set[str] = set()
    assumptions: list[dict[str, Any]] = []
    for run in run_items:
        observed_at = _record_time(run, "run")
        for model, usage in _run_model_usage(run):
            models.add(model)
            observed_usage = True
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            total_tokens += usage["total_tokens"]
            price = _price_for(catalog, model, observed_at)
            if price is None:
                unpriced_models.add(model)
                continue
            currencies.add(price["currency"])
            cost_total += (usage["input_tokens"] / 1_000_000) * price["input_per_1m"]
            cost_total += (usage["output_tokens"] / 1_000_000) * price["output_per_1m"]
            assumptions.append(
                {
                    "kind": "model_price",
                    "model": model,
                    "version": price["version"],
                    "source": price["source"],
                    "formula": "input_tokens/1_000_000*input_per_1m + output_tokens/1_000_000*output_per_1m",
                    "status": "configured",
                }
            )
    if not observed_usage:
        cost_status = "unknown"
        total = None
    elif unpriced_models or len(currencies) != 1:
        cost_status = "partial"
        total = None
    else:
        cost_status = "complete"
        total = _money(cost_total)
    return {
        "usage": {
            "runs": len(run_items),
            "input_tokens": input_tokens if observed_usage else None,
            "output_tokens": output_tokens if observed_usage else None,
            "total_tokens": total_tokens if observed_usage else None,
            "models": sorted(models),
        },
        "cost": {"total": total, "status": cost_status, "currency": next(iter(currencies)) if len(currencies) == 1 else None, "unpriced_models": sorted(unpriced_models)},
        "assumptions": _dedupe_assumptions(assumptions),
    }


def _run_model_usage(run: Mapping[str, Any]) -> list[tuple[str, dict[str, int]]]:
    output: list[tuple[str, dict[str, int]]] = []
    models = run.get("models") if isinstance(run.get("models"), list) else []
    for item in models:
        if not isinstance(item, Mapping):
            continue
        usage = _token_usage(item.get("usage"))
        if usage is not None:
            output.append((str(item.get("model") or item.get("model_name") or "unknown_model"), usage))
    if output:
        return output
    usage = _token_usage(run.get("tokens"))
    return [("unknown_model", usage)] if usage is not None else []


def _token_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    input_value = value.get("input_tokens", value.get("prompt_tokens", value.get("prompt")))
    output_value = value.get("output_tokens", value.get("completion_tokens", value.get("completion")))
    total_value = value.get("total_tokens", value.get("total"))
    if not any(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (input_value, output_value, total_value)):
        return None
    input_tokens = int(input_value or 0)
    output_tokens = int(output_value or 0)
    total_tokens = int(total_value if total_value is not None else input_tokens + output_tokens)
    return {"input_tokens": max(0, input_tokens), "output_tokens": max(0, output_tokens), "total_tokens": max(0, total_tokens)}


def _price_for(catalog: list[dict[str, Any]], model: str, observed_at: datetime | None) -> dict[str, Any] | None:
    if observed_at is None:
        return None
    matching = [item for item in catalog if item["model"] == model and item["effective_from"] <= observed_at and (item["effective_to"] is None or observed_at < item["effective_to"])]
    return max(matching, key=lambda item: item["effective_from"]) if matching else None


def _source_linked_observed_outcome(item: Mapping[str, Any], window: Mapping[str, str]) -> bool:
    observed_at = _parse_utc_or_none(item.get("observed_at"))
    return bool(
        item.get("provenance") == "observed"
        and item.get("observed_value") is not None
        and isinstance(item.get("source"), Mapping)
        and any(str(value or "").strip() for value in item["source"].values())
        and observed_at is not None
        and _in_window(observed_at, window)
    )


def _independently_verified(item: Mapping[str, Any]) -> bool:
    verification = item.get("verification") if isinstance(item.get("verification"), Mapping) else {}
    reviewer = verification.get("reviewer") if isinstance(verification.get("reviewer"), Mapping) else {}
    reviewer_id = _trusted_actor_id(reviewer)
    subject_id = _trusted_actor_id(item.get("actor"))
    return bool(verification.get("status") == "verified" and verification.get("verification_event_id") and reviewer_id and reviewer_id != subject_id)


def _business_value(outcomes: list[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not outcomes:
        return None, []
    values: list[tuple[float, str, dict[str, Any]]] = []
    assumptions: list[dict[str, Any]] = []
    for item in outcomes:
        value = item.get("business_value") if isinstance(item.get("business_value"), Mapping) else {}
        amount = value.get("value")
        currency = str(value.get("currency") or "").upper()
        source = str(value.get("source") or "").strip()
        formula = str(value.get("formula") or "").strip()
        status = str(value.get("status") or "").strip()
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and currency and source and formula and status:
            values.append((float(amount), currency, dict(value)))
            assumptions.append({"kind": "business_value", "source": source, "formula": formula, "status": status})
    currencies = {currency for _, currency, _ in values}
    total = _money(sum(amount for amount, _, _ in values)) if values and len(currencies) == 1 else None
    return {"total": total, "currency": next(iter(currencies)) if len(currencies) == 1 else None, "status": "measured" if total is not None else "not_monetized"}, assumptions


def _records_in_window(workspace_id: str, records: Iterable[Mapping[str, Any]], window: Mapping[str, str], *, kind: str) -> list[Mapping[str, Any]]:
    matched = []
    for item in records:
        if len(matched) >= MAX_RECORDS_PER_KIND:
            break
        if not isinstance(item, Mapping) or str(item.get("workspace_id") or "") != str(workspace_id):
            continue
        observed_at = _record_time(item, kind)
        if observed_at is not None and _in_window(observed_at, window):
            matched.append(item)
    return matched


def _record_time(item: Mapping[str, Any], kind: str) -> datetime | None:
    fields = {"run": ("completed_at", "updated_at", "started_at", "time"), "message": ("created_at", "updated_at", "time"), "task": ("created_at", "updated_at", "time")}[kind]
    return next((parsed for field in fields if (parsed := _parse_utc_or_none(item.get(field))) is not None), None)


def _in_window(value: datetime, window: Mapping[str, str]) -> bool:
    return _parse_utc(window["from"], "from") <= value < _parse_utc(window["to"], "to")


def _membership_index(memberships: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in memberships:
        if not isinstance(item, Mapping) or str(item.get("status") or "active").lower() != "active":
            continue
        actor_id = _trusted_actor_id(item)
        if actor_id:
            index[actor_id] = {"actor_id": actor_id, "email": _text(item.get("email")), "name": _text(item.get("name") or item.get("user")), "status": "active"}
    return index


def _actor_row(rows: dict[str, dict[str, Any]], actor_id: str | None, memberships: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not actor_id:
        return None
    if actor_id not in rows:
        member = memberships.get(actor_id)
        if member is None:
            member = {"actor_id": f"actor_{hashlib.sha256(actor_id.encode('utf-8')).hexdigest()[:16]}", "email": None, "name": None, "status": "unknown_or_departed"}
        rows[actor_id] = {"member": member, "runs": 0, "messages": 0, "tasks": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "models": {}, "cost_total": 0.0, "cost_status": "unknown"}
    return rows[actor_id]


def _trusted_actor_id(value: Any) -> str | None:
    actor = value if isinstance(value, Mapping) else {}
    actor_id = _text(actor.get("actor_id"))
    return actor_id[:240] if actor_id else None


def _parse_utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RoiWindowError(f"{name} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RoiWindowError(f"{name} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RoiWindowError(f"{name} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _parse_utc_or_none(value: Any) -> datetime | None:
    try:
        return _parse_utc(value, "timestamp")
    except RoiWindowError:
        return None


def _nonnegative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative number")
    return float(value)


def _dedupe_assumptions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for item in items:
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _money(value: float) -> float:
    return round(value, 6)


__all__ = ["RoiWindowError", "build_roi_snapshot", "load_price_catalog", "member_chargeback", "parse_time_window"]
