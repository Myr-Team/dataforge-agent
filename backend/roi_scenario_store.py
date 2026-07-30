from __future__ import annotations

import json
import math
import re
import threading
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

try:
    from .blob_store import BlobJsonReadError, blob_configured, download_blob_json_strict, upload_blob_json
    from .identity import public_actor
    from .run_store import get_run
    from .finops.roi_economics import calculate_roi
except ImportError:
    from blob_store import BlobJsonReadError, blob_configured, download_blob_json_strict, upload_blob_json
    from identity import public_actor
    from run_store import get_run
    from finops.roi_economics import calculate_roi


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "generated-outputs" / "roi-scenarios"
SCENARIO_BLOB_PREFIX = "roi-scenarios"
_LOCK = threading.RLock()
_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_SCENARIO_ID = re.compile(r"^roi_scenario_[a-f0-9]{16}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class ScenarioPersistenceError(RuntimeError):
    """Raised when a configured durable scenario store cannot be read or written."""


class ScenarioRevisionConflict(RuntimeError):
    """Raised when an immutable scenario revision already has a successor."""


def upsert_demo_roi_scenario(
    workspace_id: str,
    payload: Mapping[str, Any],
    actor: Mapping[str, Any] | None,
    *,
    seed_key: str,
) -> dict[str, Any]:
    workspace = _workspace_id(workspace_id)
    clean_seed_key = _identifier(seed_key, "seed_key", required=True)
    data = dict(payload or {})
    data.pop("seed_batch", None)
    inputs = _scenario_inputs(data)
    scenario_id = (
        "roi_scenario_"
        + hashlib.sha256(
            f"{workspace}:{clean_seed_key}".encode("utf-8")
        ).hexdigest()[:16]
    )
    with _LOCK:
        scenarios = list_roi_scenarios(workspace)
        existing = next(
            (
                item
                for item in scenarios
                if item.get("scenario_id") == scenario_id
            ),
            None,
        )
        now = _now()
        result = _scenario_result(inputs)
        scenario = {
            "scenario_id": scenario_id,
            "workspace_id": workspace,
            "title": inputs.pop("title"),
            "status": "estimated",
            "revision": int((existing or {}).get("revision") or 1),
            "previous_id": None,
            "inputs": inputs,
            "result": result,
            "formula_revision": result.get("formula_revision"),
            "actor": public_actor(dict(actor or {})),
            "created_at": str((existing or {}).get("created_at") or now),
            "updated_at": now,
            "seed_batch": clean_seed_key,
        }
        scenarios = [
            item
            for item in scenarios
            if item.get("scenario_id") != scenario_id
        ]
        scenarios.append(scenario)
        _persist(workspace, scenarios)
    return scenario


def create_roi_scenario(
    workspace_id: str,
    payload: Mapping[str, Any],
    actor: Mapping[str, Any] | None,
    *,
    previous_id: str | None = None,
    base_revision: int | None = None,
) -> dict[str, Any]:
    workspace = _workspace_id(workspace_id)
    inputs = _scenario_inputs(payload)
    if inputs["linked_run_id"] and not _linked_run_is_valid(workspace, inputs["linked_run_id"]):
        raise ValueError("linked_run_id must reference a run in the workspace")
    previous = _identifier(previous_id, "previous_id", required=False)
    with _LOCK:
        scenarios = list_roi_scenarios(workspace)
        prior = next((item for item in scenarios if item["scenario_id"] == previous), None) if previous else None
        if previous and prior is None:
            raise ValueError("previous scenario is unavailable in this workspace")
        if previous and base_revision is not None:
            if int(prior.get("revision") or 0) != int(base_revision):
                raise ScenarioRevisionConflict("ROI scenario revision has changed")
            if any(item.get("previous_id") == previous for item in scenarios):
                raise ScenarioRevisionConflict("ROI scenario revision has changed")
        revision = int(prior.get("revision") or 0) + 1 if prior else 1
        now = _now()
        result = _scenario_result(inputs)
        scenario = {
            "scenario_id": f"roi_scenario_{uuid4().hex[:16]}",
            "workspace_id": workspace,
            "title": inputs.pop("title"),
            "status": "estimated",
            "revision": revision,
            "previous_id": previous,
            "inputs": inputs,
            "result": result,
            "formula_revision": result.get("formula_revision") or result.get("formula_version"),
            "actor": public_actor(dict(actor or {})),
            "created_at": now,
            "updated_at": now,
        }
        scenarios.append(scenario)
        _persist(workspace, scenarios)
    return scenario


def list_roi_scenarios(workspace_id: str) -> list[dict[str, Any]]:
    workspace = _workspace_id(workspace_id)
    by_id: dict[str, dict[str, Any]] = {}
    local = _read_local(workspace)
    remote = _read_remote(workspace)
    for source in (local, remote):
        for item in source.get("scenarios") or [] if isinstance(source, Mapping) else []:
            if not isinstance(item, Mapping):
                continue
            scenario_id = str(item.get("scenario_id") or "")
            if not _SCENARIO_ID.fullmatch(scenario_id) or str(item.get("workspace_id") or "") != workspace:
                continue
            current = by_id.get(scenario_id)
            if current is None or str(item.get("updated_at") or "") > str(current.get("updated_at") or ""):
                by_id[scenario_id] = dict(item)
    return sorted(by_id.values(), key=lambda item: (int(item.get("revision") or 0), str(item.get("created_at") or "")))


def scenario_projection(workspace_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    workspace = _workspace_id(workspace_id)
    if str(scenario.get("workspace_id") or "") != workspace:
        raise ValueError("scenario does not belong to the workspace")
    scenario_id = str(scenario.get("scenario_id") or "")
    if not _SCENARIO_ID.fullmatch(scenario_id):
        raise ValueError("scenario_id is invalid")
    actor = scenario.get("actor") if isinstance(scenario.get("actor"), Mapping) else {}
    public_actor_view = {
        key: str(actor.get(key) or "")
        for key in ("name", "actor_id", "source")
        if str(actor.get(key) or "").strip()
    }
    return {
        "scenario_id": scenario_id,
        "title": str(scenario.get("title") or ""),
        "status": "estimated",
        "revision": int(scenario.get("revision") or 0),
        "previous_id": scenario.get("previous_id") or None,
        "inputs": _project_inputs(scenario.get("inputs")),
        "result": _project_result(scenario.get("result")),
        "formula_revision": str(scenario.get("formula_revision") or ""),
        "actor": public_actor_view,
        "created_at": str(scenario.get("created_at") or ""),
        "updated_at": str(scenario.get("updated_at") or ""),
    }


def _scenario_inputs(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    common = {
        "title",
        "currency",
        "linked_run_id",
        "evidence_revision",
    }
    legacy = {
        "expected_revenue",
        "expected_avoided_cost",
        "pilot_cost",
        "expected_saved_hours",
        "time_horizon_days",
    }
    dataforge = {
        "hours_saved",
        "hourly_value",
        "avoided_loss_or_revenue",
        "implementation_cost",
        "monthly_fixed_cost",
        "model_cost",
        "evaluation_months",
    }
    if set(data) - common - legacy - dataforge:
        raise ValueError("scenario contains unsupported fields")
    title = _text(data.get("title"), "title", 160)
    currency = _text(data.get("currency"), "currency", 3).upper()
    if not _CURRENCY.fullmatch(currency):
        raise ValueError("currency must be an ISO 4217 code")
    if set(data) & dataforge:
        if set(data) & legacy:
            raise ValueError("scenario cannot mix legacy and DataForge ROI inputs")
        return {
            "title": title,
            "currency": currency,
            "hours_saved": _money(data.get("hours_saved"), "hours_saved"),
            "hourly_value": _money(data.get("hourly_value"), "hourly_value"),
            "avoided_loss_or_revenue": _money(
                data.get("avoided_loss_or_revenue"),
                "avoided_loss_or_revenue",
            ),
            "implementation_cost": _money(
                data.get("implementation_cost"),
                "implementation_cost",
            ),
            "monthly_fixed_cost": _money(
                data.get("monthly_fixed_cost"),
                "monthly_fixed_cost",
            ),
            "model_cost": _money(data.get("model_cost", 0), "model_cost"),
            "evaluation_months": _positive_int(
                data.get("evaluation_months"),
                "evaluation_months",
                120,
            ),
            "linked_run_id": _identifier(
                data.get("linked_run_id"),
                "linked_run_id",
                required=False,
            ),
            "evidence_revision": _nonnegative_int(
                data.get("evidence_revision"),
                "evidence_revision",
            ),
        }
    return {
        "title": title,
        "currency": currency,
        "expected_revenue": _money(data.get("expected_revenue"), "expected_revenue"),
        "expected_avoided_cost": _money(data.get("expected_avoided_cost"), "expected_avoided_cost"),
        "pilot_cost": _money(data.get("pilot_cost"), "pilot_cost"),
        "expected_saved_hours": _optional_nonnegative(data.get("expected_saved_hours"), "expected_saved_hours"),
        "time_horizon_days": _positive_int(data.get("time_horizon_days"), "time_horizon_days", 3650),
        "linked_run_id": _identifier(data.get("linked_run_id"), "linked_run_id", required=False),
        "evidence_revision": _nonnegative_int(data.get("evidence_revision"), "evidence_revision"),
    }


def _scenario_result(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if "hours_saved" in inputs:
        calculation = calculate_roi(
            hours_saved=inputs["hours_saved"],
            hourly_value=inputs["hourly_value"],
            avoided_loss_or_revenue=inputs["avoided_loss_or_revenue"],
            implementation_cost=inputs["implementation_cost"],
            monthly_fixed_cost=inputs["monthly_fixed_cost"],
            model_cost=inputs["model_cost"],
            evaluation_months=inputs["evaluation_months"],
        )
        return {
            "status": "estimated",
            "currency": str(inputs["currency"]),
            **asdict(calculation),
        }
    value = float(inputs["expected_revenue"]) + float(inputs["expected_avoided_cost"])
    pilot_cost = float(inputs["pilot_cost"])
    return {
        "status": "estimated",
        "currency": str(inputs["currency"]),
        "estimated_business_value": value,
        "pilot_cost": pilot_cost,
        "net_value": value - pilot_cost,
        "roi_ratio": None if pilot_cost == 0 else round((value - pilot_cost) / pilot_cost, 6),
        "saved_hours": inputs.get("expected_saved_hours"),
        "formula_version": "roi-scenario-v1",
    }


def _project_inputs(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value.get(key)
        for key in (
            "currency",
            "expected_revenue",
            "expected_avoided_cost",
            "pilot_cost",
            "expected_saved_hours",
            "time_horizon_days",
            "linked_run_id",
            "evidence_revision",
            "hours_saved",
            "hourly_value",
            "avoided_loss_or_revenue",
            "implementation_cost",
            "monthly_fixed_cost",
            "model_cost",
            "evaluation_months",
        )
        if value.get(key) is not None
    }


def _project_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value.get(key)
        for key in (
            "status",
            "currency",
            "estimated_business_value",
            "pilot_cost",
            "net_value",
            "roi_ratio",
            "saved_hours",
            "formula_version",
            "monthly_benefit",
            "implementation_amortization",
            "monthly_total_cost",
            "monthly_net_benefit",
            "payback_months",
            "formula_revision",
        )
        if value.get(key) is not None or key == "roi_ratio"
    }


def _linked_run_is_valid(workspace_id: str, run_id: str) -> bool:
    try:
        return str(get_run(run_id).get("workspace_id") or "") == workspace_id
    except (FileNotFoundError, ValueError):
        return False


def _persist(workspace_id: str, scenarios: list[dict[str, Any]]) -> None:
    value = {
        "version": 1,
        "workspace_id": workspace_id,
        "updated_at": _now(),
        "scenarios": scenarios,
    }
    durable_store = blob_configured()
    if durable_store:
        try:
            upload_blob_json(_blob_name(workspace_id), value)
        except Exception as exc:
            raise ScenarioPersistenceError("ROI scenario persistence is unavailable") from exc
    path = _local_path(workspace_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        if not durable_store:
            raise ScenarioPersistenceError("ROI scenario persistence is unavailable") from exc


def _read_remote(workspace_id: str) -> dict[str, Any]:
    if not blob_configured():
        return {}
    try:
        value = download_blob_json_strict(_blob_name(workspace_id))
    except BlobJsonReadError as exc:
        raise ScenarioPersistenceError("ROI scenario persistence is unavailable") from exc
    return value if isinstance(value, dict) else {}


def _read_local(workspace_id: str) -> dict[str, Any]:
    path = _local_path(workspace_id)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _local_path(workspace_id: str) -> Path:
    return SCENARIO_DIR / f"{workspace_id}.json"


def _blob_name(workspace_id: str) -> str:
    return f"{SCENARIO_BLOB_PREFIX}/{workspace_id}.json"


def _workspace_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _WORKSPACE_ID.fullmatch(text):
        raise ValueError("workspace_id is invalid")
    return text


def _identifier(value: Any, field: str, *, required: bool) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if len(text) > 160 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", text):
        raise ValueError(f"{field} is invalid")
    return text


def _text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{field} is required and must be at most {limit} characters")
    return text


def _money(value: Any, field: str) -> float:
    number = _optional_nonnegative(value, field)
    if number is None:
        raise ValueError(f"{field} is required")
    return number


def _optional_nonnegative(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _positive_int(value: Any, field: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if number < 1 or number > maximum:
        raise ValueError(f"{field} must be between 1 and {maximum}")
    return number


def _nonnegative_int(value: Any, field: str) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if number < 0 or number > 1_000_000:
        raise ValueError(f"{field} is out of range")
    return number


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["SCENARIO_DIR", "ScenarioPersistenceError", "create_roi_scenario", "list_roi_scenarios", "scenario_projection"]
