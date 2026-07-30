from __future__ import annotations

from pathlib import Path
import json

import pytest

import backend.control_plane as control_plane
import backend.outcome_store as outcome_store
from backend.app import app
from fastapi.testclient import TestClient
from auth_fixtures import active_member, install_workspace_memberships, trusted_headers


def _configure_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outcome_store, "OUTCOME_DIR", tmp_path / "outcomes")
    monkeypatch.setattr(outcome_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(outcome_store, "upload_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(outcome_store, "source_is_valid", lambda *_args, **_kwargs: True)


def _actor(name: str = "Owner") -> dict[str, str]:
    return {
        "name": name,
        "email": f"{name.lower()}@contoso.com",
        "actor_id": f"oid-{name.lower()}",
        "tenant_id": "tenant-a",
        "source": "easy_auth",
    }


def _easy_headers(name: str) -> dict[str, str]:
    return trusted_headers(
        actor_id=f"oid-{name.lower()}",
        tenant_id="tenant-a",
        email=f"{name.lower()}@contoso.com",
    )


def _observed_payload() -> dict[str, object]:
    return {
        "metric_name": "pilot_conversion_rate",
        "unit": "percent",
        "baseline_value": 4.2,
        "target_value": 6.0,
        "observed_value": 5.4,
        "observed_at": "2026-07-12T02:00:00Z",
        "attribution_window_days": 14,
        "provenance": "observed",
        "source": {"file_id": "feedback-v2.csv", "run_id": "run-v2"},
    }


def test_observed_outcome_requires_source_lineage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    payload = _observed_payload()
    payload["source"] = {}

    with pytest.raises(ValueError, match="source lineage"):
        outcome_store.record_outcome_event("ws-roi", payload, _actor())


def test_observed_outcome_rejects_forged_cross_workspace_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setattr(outcome_store, "source_is_valid", lambda *_args, **_kwargs: False)

    with pytest.raises(ValueError, match="real same-workspace"):
        outcome_store.record_outcome_event("ws-roi", _observed_payload(), _actor())


def test_client_cannot_create_verified_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    payload = _observed_payload()
    payload["verification"] = {"status": "verified"}

    with pytest.raises(ValueError, match="verified"):
        outcome_store.record_outcome_event("ws-roi", payload, _actor())


def test_cost_value_artifact_count_scans_the_complete_selected_window(monkeypatch: pytest.MonkeyPatch) -> None:
    summaries = [
        {
            "run_id": f"run-{index:03d}",
            "workspace_id": "ws-roi",
            "completed_at": (
                "2026-06-12T02:00:00Z"
                if index == 80
                else "2026-07-12T02:00:00Z"
            ),
        }
        for index in range(81)
    ]

    def get_run(run_id: str) -> dict[str, object]:
        proposal = (
            {
                "artifact_urls": {"pdf": "/api/artifacts/roi-report.pdf"},
                "artifact_generated_at": {"pdf": "2026-07-20T02:00:00Z"},
            }
            if run_id == "run-080"
            else {}
        )
        return {
            "run_id": run_id,
            "workspace_id": "ws-roi",
            "completed_at": (
                "2026-06-12T02:00:00Z"
                if run_id == "run-080"
                else "2026-07-12T02:00:00Z"
            ),
            "artifact": {"proposal": proposal},
        }

    monkeypatch.setattr(control_plane, "list_runs", lambda _workspace_id: summaries)
    monkeypatch.setattr(control_plane, "get_run", get_run)
    monkeypatch.setattr(
        control_plane,
        "get_artifact",
        lambda _name: {
            "artifact_name": "roi-report.pdf",
            "workspace_id": "ws-roi",
            "kind": "pdf",
            "status": "ready",
            "content_type": "application/pdf",
            "bytes": 128,
        },
    )
    monkeypatch.setattr(control_plane, "list_artifact_jobs", lambda _workspace_id: [])
    monkeypatch.setattr(control_plane, "list_tasks", lambda _workspace_id: [])
    monkeypatch.setattr(
        control_plane,
        "workspace_roi_snapshot",
        lambda *_args: {
            "window": {"from": "2026-07-01T00:00:00Z", "to": "2026-07-31T23:59:59Z"},
            "cost_evidence": {"status": "complete", "total": 1, "currency": "USD"},
            "outcome_evidence": {"status": "not_recorded"},
            "foundry_integration": {},
            "generated_at": "2026-07-31T23:59:59Z",
        },
    )
    monkeypatch.setattr(control_plane, "realized_roi_evidence", lambda _snapshot: {"status": "not_recorded"})
    monkeypatch.setattr(control_plane, "list_roi_scenarios", lambda _workspace_id: [])

    result = control_plane.workspace_cost_value_snapshot(
        "ws-roi",
        "2026-07-01T00:00:00Z",
        "2026-07-31T23:59:59Z",
    )

    assert result["artifact_count"] == 1


def test_outcome_verification_is_a_separate_reviewer_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    event = outcome_store.record_outcome_event("ws-roi", _observed_payload(), _actor())

    assert event["provenance"] == "observed"
    assert event["verification"]["status"] == "unverified"

    verified = outcome_store.verify_outcome_event(
        "ws-roi",
        event["event_id"],
        _actor("Reviewer"),
        note="Source file and attribution window reviewed.",
    )

    assert verified["verification"]["status"] == "verified"
    assert verified["verification"]["verification_event_id"].startswith("verification_")
    assert verified["verification"]["reviewer"]["actor_id"] == "oid-reviewer"
    assert outcome_store.list_outcome_events("ws-roi")[0]["event_id"] == event["event_id"]
    verification_event = outcome_store.list_verification_events("ws-roi")[0]
    assert verification_event["outcome_event_id"] == event["event_id"]
    assert verification_event["kind"] == "outcome_verification"


def test_roi_states_follow_outcome_evidence() -> None:
    usage = {
        "totals": {
            "runs": 1,
            "agent_runs": 1,
            "snapshot_runs": 0,
            "known_usage_runs": 1,
            "unknown_usage_runs": 0,
            "total_tokens": 1000,
            "usage_status": "complete",
        }
    }
    audit = {"events": []}

    estimated = control_plane._workspace_roi_summary(usage, audit, [])
    assert estimated["status"] == "estimated"

    observed = {
        **_observed_payload(),
        "event_id": "outcome-observed",
        "actor": {"actor_id": "oid-owner"},
        "verification": {"status": "unverified"},
    }
    measured = control_plane._workspace_roi_summary(usage, audit, [observed])
    assert measured["status"] == "measured"
    assert measured["outcomes"]["observed_count"] == 1

    verified_event = {
        **observed,
        "verification": {
            "status": "verified",
            "verified_at": "2026-07-12T03:00:00Z",
            "verification_event_id": "verification-1",
            "reviewer": {"actor_id": "oid-reviewer"},
        },
    }
    verified = control_plane._workspace_roi_summary(usage, audit, [verified_event])
    assert verified["status"] == "verified"
    assert verified["outcomes"]["verified_count"] == 1


def test_outcome_verification_requires_independent_actor_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    event = outcome_store.record_outcome_event("ws-roi", _observed_payload(), _actor())

    with pytest.raises(ValueError, match="independent"):
        outcome_store.verify_outcome_event("ws-roi", event["event_id"], _actor())


def test_outcome_verification_compares_canonical_tenant_and_actor_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    event = outcome_store.record_outcome_event("ws-roi", _observed_payload(), {**_actor(), "actor_id": "OID-Owner", "tenant_id": "Tenant-A"})

    with pytest.raises(ValueError, match="independent"):
        outcome_store.verify_outcome_event("ws-roi", event["event_id"], {**_actor("Reviewer"), "actor_id": "oid-owner", "tenant_id": "tenant-a"})


def test_outcome_verification_rejects_missing_tenant_on_outcome_or_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    no_tenant_outcome = outcome_store.record_outcome_event("ws-roi", _observed_payload(), {**_actor(), "tenant_id": ""})
    with pytest.raises(ValueError, match="trusted"):
        outcome_store.verify_outcome_event("ws-roi", no_tenant_outcome["event_id"], _actor("Reviewer"))

    tenant_outcome = outcome_store.record_outcome_event("ws-roi", _observed_payload(), _actor("Second"))
    with pytest.raises(ValueError, match="reviewer"):
        outcome_store.verify_outcome_event("ws-roi", tenant_outcome["event_id"], {**_actor("Reviewer"), "tenant_id": ""})


def test_source_reference_requires_exact_workspace_bound_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setattr(outcome_store, "get_run", lambda run_id: {"workspace_id": "ws-roi"} if run_id == "run-exact" else (_ for _ in ()).throw(FileNotFoundError(run_id)))
    monkeypatch.setattr(outcome_store, "get_workspace_detail", lambda _ws: {"documents": [{"source_file": "feedback.csv"}]})
    monkeypatch.setattr(outcome_store, "get_artifact_job", lambda job_id: {"workspace_id": "ws-roi", "job_id": job_id} if job_id == "artifact_job_exact" else (_ for _ in ()).throw(FileNotFoundError(job_id)))

    assert outcome_store._source_reference_exists("ws-roi", "run_id", "run-exact")
    assert not outcome_store._source_reference_exists("ws-roi", "run_id", "run-exact.json")
    assert not outcome_store._source_reference_exists("ws-roi", "file_id", "feedback.csv")
    assert outcome_store._source_reference_exists("ws-roi", "file_id", outcome_store.hashlib.sha256(b"feedback.csv").hexdigest()[:16])
    assert outcome_store._source_reference_exists("ws-roi", "artifact_id", "artifact_job_exact")
    assert not outcome_store._source_reference_exists("ws-roi", "artifact_id", "artifact_job_exact.json")


def test_business_value_requires_source_formula_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    payload = _observed_payload()
    payload["business_value"] = {"value": 100, "currency": "USD", "source": "finance-ledger"}

    with pytest.raises(ValueError, match="business_value"):
        outcome_store.record_outcome_event("ws-roi", payload, _actor())


def test_business_value_rejects_negative_nonfinite_and_noncanonical_currency_before_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    for value, currency in ((-1, "USD"), (float("inf"), "USD"), (1, "usd")):
        payload = _observed_payload()
        payload["business_value"] = {"value": value, "currency": currency, "source": "ledger", "formula": "margin", "status": "measured"}
        with pytest.raises(ValueError, match="business_value"):
            outcome_store.record_outcome_event("ws-roi", payload, _actor())


def test_synthetic_outcome_does_not_promote_roi_state() -> None:
    usage = {"totals": {"runs": 0, "agent_runs": 0, "snapshot_runs": 0}}
    synthetic = {
        "event_id": "outcome-synthetic",
        "metric_name": "simulated_conversion_rate",
        "unit": "percent",
        "observed_value": 8.0,
        "provenance": "synthetic",
        "verification": {"status": "unverified"},
    }

    result = control_plane._workspace_roi_summary(usage, {"events": []}, [synthetic])

    assert result["status"] == "estimated"
    assert result["outcomes"]["synthetic_count"] == 1


def test_demo_outcomes_are_idempotent_source_linked_and_never_self_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    values = (
        {
            "metric_name": "analysis_cycle_hours",
            "unit": "hours",
            "baseline_value": 16,
            "observed_value": 11.5,
            "observed_at": "2026-07-26T02:00:00Z",
            "provenance": "observed",
            "verification_state": "verified",
            "source": {"run_id": "run-demo-one"},
        },
        {
            "metric_name": "manual_review_hours",
            "unit": "hours",
            "baseline_value": 24,
            "observed_value": 15,
            "observed_at": "2026-07-28T02:00:00Z",
            "provenance": "observed",
            "source": {"run_id": "run-demo-two"},
        },
    )

    first = outcome_store.upsert_demo_outcome_events(
        "ws-roi",
        values,
        seed_key="operations-v1",
    )
    second = outcome_store.upsert_demo_outcome_events(
        "ws-roi",
        values[:1],
        seed_key="operations-v2",
    )

    assert first == {"created": 2, "updated": 0, "seed_batch": "operations-v1"}
    assert second == {"created": 0, "updated": 1, "seed_batch": "operations-v2"}
    [persisted] = outcome_store.list_outcome_events("ws-roi")
    assert persisted["source"] == {"run_id": "run-demo-one"}
    assert persisted["verification"] == {"status": "unverified"}
    assert persisted["trusted_identity"] is False
    assert persisted["demo_seed"] == {
        "owner": "operations_demo",
        "batch": "operations-v2",
    }


def test_outcome_api_persists_lists_and_verifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_MEMBER_PSEUDONYM_SALT", "outcome-api-projection-salt")
    monkeypatch.setenv("DF_ENVIRONMENT", "test")
    install_workspace_memberships(
        monkeypatch,
        {
            "ws-roi": [
                active_member("oid-owner", "tenant-a", "owner"),
                active_member("oid-reviewer", "tenant-a", "admin"),
            ]
        },
    )
    client = TestClient(app)
    owner_headers = _easy_headers("Owner")
    reviewer_headers = _easy_headers("Reviewer")

    created = client.post("/api/workspaces/ws-roi/outcomes", json=_observed_payload(), headers=owner_headers)
    assert created.status_code == 200
    event_id = created.json()["event"]["event_id"]

    listed = client.get("/api/workspaces/ws-roi/outcomes", headers=owner_headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    verified = client.post(
        f"/api/workspaces/ws-roi/outcomes/{event_id}/verify",
        json={"note": "Reviewed against the imported feedback file."},
        headers=reviewer_headers,
    )
    assert verified.status_code == 200
    assert verified.json()["event"]["verification"]["status"] == "verified"

    relisted = client.get("/api/workspaces/ws-roi/outcomes", headers=owner_headers)
    assert relisted.status_code == 200
    expected_owner_label = control_plane.member_subject_label("ws-roi", _actor())
    expected_reviewer_label = control_plane.member_subject_label("ws-roi", _actor("Reviewer"))
    assert created.json()["event"]["actor"] == {"subject_label": expected_owner_label}
    assert relisted.json()["events"][0]["actor"] == {"subject_label": expected_owner_label}
    assert verified.json()["event"]["verification"]["reviewer"] == {"subject_label": expected_reviewer_label}
    assert verified.json()["event"]["verification"]["event"]["actor"] == {"subject_label": expected_reviewer_label}

    forbidden = (
        "owner@contoso.com", "oid-owner", "Owner",
        "reviewer@contoso.com", "oid-reviewer", "Reviewer", "tenant-a",
    )
    for response in (created, listed, verified, relisted):
        serialized = json.dumps(response.json(), sort_keys=True)
        for raw_identity in forbidden:
            assert raw_identity not in serialized, (response.request.url.path, raw_identity, serialized)

    persisted = outcome_store.list_outcome_events("ws-roi")[0]
    assert persisted["actor"]["email"] == "owner@contoso.com"
    assert persisted["verification"]["reviewer"]["actor_id"] == "oid-reviewer"
    assert persisted["verification"]["event"]["actor"]["tenant_id"] == "tenant-a"
