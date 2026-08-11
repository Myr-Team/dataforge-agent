from __future__ import annotations

import pytest

import backend.run_store as run_store
from backend.local_agent_observation import build_local_model_observation


def test_local_observation_projects_only_safe_model_facts() -> None:
    run = {
        "run_id": "run-sensitive-name",
        "workspace_id": "workspace-sensitive-name",
        "status": "completed",
        "actor": {"email": "member@example.test"},
        "models": [
            {
                "agent": "df-feasibility-analyst",
                "execution_kind": "full_analysis",
                "provider_type": "deepseek",
                "provider_id": "provider-primary",
                "model_id": "deepseek-v4-pro",
                "route": "deepseek-primary",
                "deployment": "deepseek-v4-pro",
                "route_evidence": "observed",
                "response_id": "response-sensitive-name",
                "usage": {
                    "prompt": 100,
                    "completion": 40,
                    "reasoning": 12,
                    "cached_input": 70,
                    "total": 140,
                },
                "provider_cache": {
                    "state": "partial_hit",
                    "hit_tokens": 70,
                    "miss_tokens": 30,
                    "hit_rate_pct": 70.0,
                    "evidence_state": "observed",
                },
                "latency_ms": 850,
                "cost_estimate": {
                    "status": "estimated",
                    "amount": 0.0123,
                    "currency": "USD",
                    "price_card_revision": 7,
                },
                "provider_body": {"secret": "must-not-survive"},
                "prompt": "must-not-survive",
                "response": "must-not-survive",
            }
        ],
    }

    observation = build_local_model_observation(run)
    payload = observation.model_dump(mode="json")

    assert payload["schema_version"] == "dataforge.local-model-observation.v1"
    assert payload["run_ref"].startswith("run_")
    assert payload["request_ref"].startswith("request_")
    assert payload["workspace_ref"].startswith("workspace_")
    assert "sensitive" not in str(payload)
    assert "member@example.test" not in str(payload)
    assert "must-not-survive" not in str(payload)
    assert payload["provider_type"] == "deepseek"
    assert payload["provider_id"] == "provider-primary"
    assert payload["route_evidence"] == "observed"
    assert payload["usage"] == {
        "input_tokens": 100,
        "output_tokens": 40,
        "reasoning_tokens": 12,
        "cached_input_tokens": 70,
        "total_tokens": 140,
    }
    assert payload["provider_cache"]["hit_rate_pct"] == 70.0
    assert payload["cost"]["status"] == "estimated"


def test_credential_shaped_provider_id_is_redacted_before_persistence_and_observation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()
    run_store.start_run("run-provider-id-redaction", "workspace-safe", "provider id test")
    run_store.record_event(
        "run-provider-id-redaction",
        "model_response",
        {
            "provider_type": "deepseek",
            "provider_id": "sk-example-token",
            "model_id": "deepseek-v4-pro",
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        },
    )
    run_store.complete_run("run-provider-id-redaction", final={"text": "done"}, artifact={})

    run = run_store.get_run("run-provider-id-redaction")
    assert "provider_id" not in run["models"][0]
    assert "sk-example-token" not in str(run)
    assert build_local_model_observation(run).provider_id is None


def test_run_usage_summary_ignores_malformed_token_counts_without_raising() -> None:
    summary = run_store._token_usage(
        {
            "models": [
                {
                    "usage": {
                        "prompt_tokens": 12.9,
                        "completion_tokens": float("nan"),
                        "total_tokens": float("inf"),
                    }
                }
            ]
        }
    )

    assert summary is None


def test_local_observation_keeps_unknown_or_unsafe_values_unavailable() -> None:
    run = {
        "run_id": "run-safe",
        "workspace_id": "workspace-safe",
        "status": "running",
        "models": [
            {
                "provider_type": "unknown-provider",
                "provider_id": "secret/value",
                "model_id": "unsafe model id",
                "route_evidence": "guessed",
                "usage": {},
                "provider_cache": {
                    "state": "hit",
                    "hit_tokens": 5,
                    "miss_tokens": None,
                    "hit_rate_pct": 100,
                    "evidence_state": "observed",
                },
            }
        ],
    }

    observation = build_local_model_observation(run)

    assert observation.provider_type is None
    assert observation.provider_id is None
    assert observation.model_id is None
    assert observation.route_evidence == "unavailable"
    assert observation.provider_cache.state == "unavailable"
    assert observation.usage.total_tokens is None
    assert observation.status == "unknown"


def test_local_observation_preserves_synthetic_provenance_without_observed_cache_claim() -> None:
    run = {
        "run_id": "synthetic-shenzhen-site-selection-0001",
        "workspace_id": "demo-corpus",
        "status": "completed",
        "models": [{
            "response_id": "req_synthetic_0001",
            "route_evidence": "synthetic",
            "provenance": "synthetic_demo",
            "usage": {"prompt": 100, "completion": 200, "total": 300},
            "provider_cache": {
                "state": "hit",
                "hit_tokens": 80,
                "miss_tokens": 20,
                "hit_rate_pct": 80.0,
                "evidence_state": "synthetic",
            },
        }],
    }

    observation = build_local_model_observation(run)

    assert observation.provenance == "synthetic_demo"
    assert observation.route_evidence == "synthetic"
    assert observation.provider_cache.evidence_state == "synthetic"


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf"), True, -0.01])
def test_local_observation_downgrades_invalid_money_to_unavailable(amount: object) -> None:
    run = {
        "run_id": "run-safe",
        "workspace_id": "workspace-safe",
        "status": "completed",
        "models": [
            {
                "cost_estimate": {
                    "status": "estimated",
                    "amount": amount,
                    "currency": "USD",
                    "price_card_revision": 7,
                }
            }
        ],
    }

    cost = build_local_model_observation(run).cost

    assert cost.status == "unavailable"
    assert cost.amount is None


@pytest.mark.parametrize("amount", [0, 0.0123])
def test_local_observation_preserves_finite_nonnegative_money(amount: float) -> None:
    run = {
        "run_id": "run-safe",
        "workspace_id": "workspace-safe",
        "status": "completed",
        "models": [
            {
                "cost_estimate": {
                    "status": "estimated",
                    "amount": amount,
                    "currency": "USD",
                    "price_card_revision": 7,
                }
            }
        ],
    }

    cost = build_local_model_observation(run).cost

    assert cost.status == "estimated"
    assert cost.amount == amount
