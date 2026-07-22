from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from .model_policy import context_optimization_gate
except ImportError:
    from model_policy import context_optimization_gate


RunLoader = Callable[[str], list[dict[str, Any]]]
SnapshotLoader = Callable[[str, str, str], dict[str, Any]]
AuditLoader = Callable[[str], dict[str, Any]]
OutcomeLoader = Callable[[str], list[dict[str, Any]]]
ChargebackLoader = Callable[[str, str, str], dict[str, Any]]
EvaluationLoader = Callable[[str], dict[str, Any]]


def build_monitor_dashboard(
    workspace_ids: list[str],
    *,
    scope: str,
    from_value: str,
    to_value: str,
    actor: dict[str, Any],
    run_loader: RunLoader,
    cost_loader: SnapshotLoader | None = None,
    audit_loader: AuditLoader | None = None,
    outcome_loader: OutcomeLoader | None = None,
    chargeback_loader: ChargebackLoader | None = None,
    evaluation_loader: EvaluationLoader | None = None,
) -> dict[str, Any]:
    workspace_ids = _ordered_workspace_ids(workspace_ids)
    rows = _run_rows(workspace_ids, from_value, to_value, run_loader)
    cost_snapshots = _load_cost_snapshots(workspace_ids, from_value, to_value, cost_loader)
    audit_snapshots = _load_audit_snapshots(workspace_ids, audit_loader)
    outcome_snapshots = _load_outcome_snapshots(workspace_ids, outcome_loader)
    chargeback_snapshots = _load_chargeback_snapshots(workspace_ids, from_value, to_value, chargeback_loader)
    summary_tokens = _usage_summary(rows)
    return {
        "scope": _scope_projection(scope, workspace_ids),
        "window": {"from": from_value, "to": to_value, "timezone": "UTC"},
        "freshness": {"generated_at": _now(), "sources": _freshness_sources(cost_loader, audit_loader, outcome_loader)},
        "summary": {
            "calls": _call_summary(rows),
            "tokens": summary_tokens,
            "cost": _cost_summary(cost_snapshots),
            "quality": _quality_summary(rows, audit_snapshots, evaluation_loader),
            "roi": _roi_summary(cost_snapshots, outcome_snapshots),
        },
        "series": {"daily": _daily_series(rows)},
        "models": _model_rows(rows),
        "routes": _route_rows(rows),
        "members": _member_rows(chargeback_snapshots, rows, actor),
        "opportunity": _opportunity(rows, summary_tokens),
        "coverage": _coverage(rows),
    }


def _ordered_workspace_ids(workspace_ids: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in workspace_ids:
        workspace_id = str(item or "").strip()
        if not workspace_id or workspace_id in seen:
            continue
        seen.add(workspace_id)
        ordered.append(workspace_id)
    return ordered


def _run_rows(
    workspace_ids: list[str],
    from_value: str,
    to_value: str,
    run_loader: RunLoader,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for workspace_id in workspace_ids:
        try:
            loaded = run_loader(workspace_id)
        except Exception:
            loaded = []
        for row in loaded or []:
            if not isinstance(row, dict):
                continue
            row_workspace_id = str(row.get("workspace_id") or workspace_id or "").strip()
            if row_workspace_id != workspace_id:
                continue
            if _in_window(row, from_value, to_value):
                rows.append(dict(row))
    rows.sort(key=lambda item: str(_row_time(item) or ""))
    return rows


def _load_cost_snapshots(
    workspace_ids: list[str],
    from_value: str,
    to_value: str,
    cost_loader: SnapshotLoader | None,
) -> list[dict[str, Any]]:
    if cost_loader is None:
        return []
    snapshots: list[dict[str, Any]] = []
    for workspace_id in workspace_ids:
        try:
            item = cost_loader(workspace_id, from_value, to_value)
        except Exception:
            item = {}
        if isinstance(item, dict):
            snapshots.append(item)
    return snapshots


def _load_audit_snapshots(workspace_ids: list[str], audit_loader: AuditLoader | None) -> list[dict[str, Any]]:
    if audit_loader is None:
        return []
    snapshots: list[dict[str, Any]] = []
    for workspace_id in workspace_ids:
        try:
            item = audit_loader(workspace_id)
        except Exception:
            item = {}
        if isinstance(item, dict):
            snapshots.append(item)
    return snapshots


def _load_outcome_snapshots(workspace_ids: list[str], outcome_loader: OutcomeLoader | None) -> list[list[dict[str, Any]]]:
    if outcome_loader is None:
        return []
    snapshots: list[list[dict[str, Any]]] = []
    for workspace_id in workspace_ids:
        try:
            item = outcome_loader(workspace_id)
        except Exception:
            item = []
        snapshots.append([row for row in item or [] if isinstance(row, dict)])
    return snapshots


def _load_chargeback_snapshots(
    workspace_ids: list[str],
    from_value: str,
    to_value: str,
    chargeback_loader: ChargebackLoader | None,
) -> list[dict[str, Any]]:
    if chargeback_loader is None:
        return []
    snapshots: list[dict[str, Any]] = []
    for workspace_id in workspace_ids:
        try:
            item = chargeback_loader(workspace_id, from_value, to_value)
        except Exception:
            item = {}
        if isinstance(item, dict):
            snapshots.append(item)
    return snapshots


def _scope_projection(scope: str, workspace_ids: list[str]) -> dict[str, Any]:
    kind = "portfolio" if scope == "portfolio" else "current"
    if kind == "portfolio":
        return {
            "kind": "portfolio",
            "workspace_ids": workspace_ids,
            "workspace_count": len(workspace_ids),
            "label": "Owned portfolio",
        }
    workspace_id = workspace_ids[0] if workspace_ids else ""
    return {
        "kind": "current",
        "workspace_id": workspace_id,
        "workspace_ids": [workspace_id] if workspace_id else [],
        "label": workspace_id,
    }


def _freshness_sources(
    cost_loader: SnapshotLoader | None,
    audit_loader: AuditLoader | None,
    outcome_loader: OutcomeLoader | None,
) -> list[str]:
    sources = ["run_store"]
    if cost_loader is not None or audit_loader is not None:
        sources.append("governance")
    if outcome_loader is not None:
        sources.append("outcomes")
    return sources


def _call_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    observed = len(rows)
    succeeded = 0
    failed = 0
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if status in {"completed", "succeeded", "success"}:
            succeeded += 1
        elif status in {"failed", "error", "cancelled", "canceled"}:
            failed += 1
    return {
        "observed": observed,
        "succeeded": succeeded,
        "failed": failed,
        "unknown": max(0, observed - succeeded - failed),
    }


def _usage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    input_total = 0
    output_total = 0
    total_total = 0
    known_runs = 0
    unknown_runs = 0
    input_complete = True
    output_complete = True
    total_complete = True
    for row in rows:
        usage = _row_usage(row)
        if usage is None:
            unknown_runs += 1
            continue
        known_runs += 1
        if usage.get("input") is None:
            input_complete = False
        else:
            input_total += int(usage["input"])
        if usage.get("output") is None:
            output_complete = False
        else:
            output_total += int(usage["output"])
        if usage.get("total") is None:
            total_complete = False
        else:
            total_total += int(usage["total"])
    return {
        "input": input_total if known_runs and input_complete else None,
        "output": output_total if known_runs and output_complete else None,
        "total": total_total if known_runs and total_complete else None,
        "known_runs": known_runs,
        "unknown_runs": unknown_runs,
    }


def _cost_summary(cost_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    if not cost_snapshots:
        return {
            "status": "unavailable",
            "amount": None,
            "currency": "USD",
            "price_catalog_version": None,
        }
    totals: list[float] = []
    currencies: set[str] = set()
    for snapshot in cost_snapshots:
        evidence = snapshot.get("cost_evidence") if isinstance(snapshot.get("cost_evidence"), dict) else {}
        if str(evidence.get("status") or "").strip().lower() != "complete":
            return {
                "status": "unavailable",
                "amount": None,
                "currency": str(evidence.get("currency") or "USD"),
                "price_catalog_version": None,
            }
        amount = evidence.get("total")
        currency = str(evidence.get("currency") or "USD")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            return {
                "status": "unavailable",
                "amount": None,
                "currency": currency,
                "price_catalog_version": None,
            }
        totals.append(float(amount))
        currencies.add(currency)
    if len(currencies) != 1:
        return {
            "status": "unavailable",
            "amount": None,
            "currency": None,
            "price_catalog_version": None,
        }
    return {
        "status": "available",
        "amount": round(sum(totals), 2),
        "currency": next(iter(currencies)),
        "price_catalog_version": None,
    }


def _quality_summary(
    rows: list[dict[str, Any]],
    audit_snapshots: list[dict[str, Any]],
    evaluation_loader: EvaluationLoader | None,
) -> dict[str, Any]:
    rework_runs = 0
    audited_runs = 0
    observed_audit_verdict = False
    for row in rows:
        audit = row.get("audit") if isinstance(row.get("audit"), dict) else {}
        verdict = str(audit.get("verdict") or audit.get("status") or "").strip().lower()
        if verdict:
            observed_audit_verdict = True
            audited_runs += 1
        if verdict in {"revise", "downgraded", "rework"}:
            rework_runs += 1
    _ = audit_snapshots
    return {
        "evidence_coverage_pct": None,
        "audited_runs": audited_runs if observed_audit_verdict else None,
        "rework_runs": rework_runs,
        "evaluator_coverage_pct": None,
        "context_optimization": _context_optimization_summary(evaluation_loader),
    }


def _roi_summary(cost_snapshots: list[dict[str, Any]], outcome_snapshots: list[list[dict[str, Any]]]) -> dict[str, Any]:
    if not cost_snapshots:
        return {
            "status": "pending_verification",
            "verified_value": None,
            "model_cost": None,
            "evaluator_cost": None,
            "roi_pct": None,
        }
    verified_values: list[float] = []
    model_costs: list[float] = []
    currency: str | None = None
    for snapshot in cost_snapshots:
        realized = snapshot.get("realized_roi") if isinstance(snapshot.get("realized_roi"), dict) else {}
        if str(realized.get("status") or "").strip().lower() != "verified":
            return {
                "status": "pending_verification",
                "verified_value": None,
                "model_cost": None,
                "evaluator_cost": None,
                "roi_pct": None,
            }
        value = realized.get("value")
        model_cost = snapshot.get("cost_evidence", {}).get("total") if isinstance(snapshot.get("cost_evidence"), dict) else None
        currency = str(realized.get("currency") or currency or "")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return {
                "status": "pending_verification",
                "verified_value": None,
                "model_cost": None,
                "evaluator_cost": None,
                "roi_pct": None,
            }
        verified_values.append(float(value))
        if isinstance(model_cost, (int, float)) and not isinstance(model_cost, bool):
            model_costs.append(float(model_cost))
    verified_total = sum(verified_values)
    model_total = sum(model_costs) if model_costs else None
    roi_pct = None
    if model_total and model_total > 0:
        roi_pct = round(((verified_total - model_total) / model_total) * 100, 2)
    _ = outcome_snapshots
    return {
        "status": "verified" if verified_values else "pending_verification",
        "verified_value": round(verified_total, 2) if verified_values else None,
        "model_cost": round(model_total, 2) if model_total is not None else None,
        "evaluator_cost": None,
        "roi_pct": roi_pct,
        "currency": currency or None,
    }


def _daily_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        timestamp = _row_time(row)
        if timestamp is None:
            continue
        day = timestamp.date().isoformat()
        bucket = buckets.setdefault(day, {"date": day, "calls": 0, "succeeded": 0, "failed": 0, "total_tokens": None})
        bucket["calls"] += 1
        status = str(row.get("status") or "").strip().lower()
        if status in {"completed", "succeeded", "success"}:
            bucket["succeeded"] += 1
        elif status in {"failed", "error", "cancelled", "canceled"}:
            bucket["failed"] += 1
        usage = _row_usage(row)
        if usage is not None:
            total_tokens = usage.get("total")
            if isinstance(total_tokens, int):
                bucket["total_tokens"] = int(bucket["total_tokens"] or 0) + total_tokens
    return [buckets[key] for key in sorted(buckets)]


def _model_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        for model in row.get("models") or []:
            if not isinstance(model, dict):
                continue
            route = _clean(model.get("route")) or _clean(row.get("route")) or "unknown"
            deployment = (
                _clean(model.get("deployment"))
                or _clean(model.get("model"))
                or _clean(model.get("deployment_name"))
                or "unknown"
            )
            if route == "unknown" and deployment == "unknown":
                continue
            usage = _usage_from_dict(model.get("usage") if isinstance(model.get("usage"), dict) else model)
            key = (deployment, route)
            group = groups.setdefault(
                key,
                {"deployment": deployment, "route": route, "calls": 0, "total_tokens": 0},
            )
            group["calls"] += 1
            group["total_tokens"] += int(usage["total"]) if usage is not None and isinstance(usage.get("total"), int) else 0
    return sorted(groups.values(), key=lambda item: (-int(item["calls"]), item["deployment"], item["route"]))


def _route_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        emitted = False
        for model in row.get("models") or []:
            if not isinstance(model, dict):
                continue
            route = _clean(model.get("route")) or _clean(row.get("route")) or "unknown"
            usage = _usage_from_dict(model.get("usage") if isinstance(model.get("usage"), dict) else model)
            group = groups.setdefault(route, {"route": route, "calls": 0, "total_tokens": 0})
            group["calls"] += 1
            group["total_tokens"] += int(usage["total"]) if usage is not None and isinstance(usage.get("total"), int) else 0
            emitted = True
        if emitted:
            continue
        route = _clean(row.get("route")) or "unknown"
        group = groups.setdefault(route, {"route": route, "calls": 0, "total_tokens": 0})
        group["calls"] += 1
        usage = _row_usage(row)
        group["total_tokens"] += int(usage["total"]) if usage is not None and isinstance(usage.get("total"), int) else 0
    return sorted(groups.values(), key=lambda item: (-int(item["calls"]), item["route"]))


def _member_rows(
    chargeback_snapshots: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    actor: dict[str, Any],
) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snapshot in chargeback_snapshots:
        for item in snapshot.get("members") or []:
            if not isinstance(item, dict):
                continue
            member = item.get("member") if isinstance(item.get("member"), dict) else {}
            subject_label = _clean(member.get("subject_label"))
            if not subject_label or subject_label in seen:
                continue
            seen.add(subject_label)
            cost = item.get("cost") if isinstance(item.get("cost"), dict) else {}
            members.append(
                {
                    "subject_label": subject_label,
                    "groups": int(item.get("groups") or 0),
                    "cost": {
                        "status": str(cost.get("status") or "unknown"),
                        "total": cost.get("total"),
                        "currency": cost.get("currency"),
                    },
                }
            )
    if members:
        return sorted(members, key=lambda item: item["subject_label"])
    _ = (rows, actor)
    return []


def _opportunity(rows: list[dict[str, Any]], usage: dict[str, Any]) -> dict[str, Any]:
    if not rows or usage.get("known_runs") in (None, 0):
        return {
            "status": "unavailable",
            "kind": None,
            "message": "No eligible optimization evidence yet.",
        }
    return {
        "status": "pending",
        "kind": None,
        "message": "More governed evidence is required before ranking optimization opportunities.",
    }


def _coverage(rows: list[dict[str, Any]]) -> dict[str, int]:
    governed_text_calls = 0
    out_of_scope_image_calls = 0
    for row in rows:
        models = [item for item in row.get("models") or [] if isinstance(item, dict)]
        if not models:
            continue
        for model in models:
            route = _clean(model.get("route")) or _clean(row.get("route"))
            deployment = (_clean(model.get("deployment")) or _clean(model.get("model"))).lower()
            if "image" in deployment:
                out_of_scope_image_calls += 1
            elif route or deployment:
                governed_text_calls += 1
    return {
        "governed_text_calls": governed_text_calls,
        "out_of_scope_image_calls": out_of_scope_image_calls,
    }


def _context_optimization_summary(evaluation_loader: EvaluationLoader | None) -> dict[str, Any]:
    loader = evaluation_loader or context_optimization_gate
    try:
        raw = loader("followup")
    except Exception:
        raw = {}
    return {
        "status": str((raw or {}).get("status") or "unavailable").strip().lower() or "unavailable",
        "sample_count": _as_int((raw or {}).get("sample_count")),
        "evaluator_version": _clean((raw or {}).get("evaluator_version")) or None,
        "eligible": bool((raw or {}).get("eligible") is True),
    }


def _row_usage(row: dict[str, Any]) -> dict[str, int] | None:
    tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else None
    usage = _usage_from_dict(tokens) if isinstance(tokens, dict) else None
    if usage is not None:
        return usage
    for model in row.get("models") or []:
        if not isinstance(model, dict):
            continue
        usage = _usage_from_dict(model.get("usage") if isinstance(model.get("usage"), dict) else model)
        if usage is not None:
            return usage
    return None


def _usage_from_dict(data: dict[str, Any] | None) -> dict[str, int | None] | None:
    if not isinstance(data, dict):
        return None
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else data
    total = _first_present_int(usage, "total", "total_tokens")
    input_value = _first_present_int(usage, "input", "prompt", "prompt_tokens", "input_tokens")
    output_value = _first_present_int(usage, "output", "completion", "completion_tokens", "output_tokens")
    if total is None and input_value is None and output_value is None:
        return None
    if total is None and input_value is not None and output_value is not None:
        total = input_value + output_value
    return {"input": input_value, "output": output_value, "total": total}


def _first_present_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in data:
            continue
        value = _as_int(data.get(key))
        if value is not None:
            return value
    return None


def _in_window(row: dict[str, Any], from_value: str, to_value: str) -> bool:
    timestamp = _row_time(row)
    start = _parse_time(from_value)
    end = _parse_time(to_value)
    if timestamp is None or start is None or end is None:
        return False
    return start <= timestamp <= end


def _row_time(row: dict[str, Any]) -> datetime | None:
    for key in ("completed_at", "updated_at", "started_at", "time", "created_at"):
        parsed = _parse_time(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _dig(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
