from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.finops.actor_ref_repair import (
    ActorRefRepairInputError,
    build_event_key_repairs,
    main,
    repair_completed_run_actor_refs,
)
from backend.finops.ingestion import ingest_completed_run
from backend.finops.models import EstimatedCost
from backend.finops.normalization import (
    canonical_tenant_ref,
    normalize_run_event,
    opaque_ref,
)
from backend.finops.repository import (
    FinOpsEventRepairConflict,
    InMemoryFinOpsRepository,
)


_SNAPSHOT_SECRET = "snapshot-secret"


def _run(
    run_id: str,
    completed_at: str,
    *,
    actor_id: str | None = "MEMBER-A",
) -> dict[str, object]:
    actor: dict[str, object] = {
        "tenant_id": "TENANT-A",
        "email": "member-a@example.test",
    }
    if actor_id is not None:
        actor["actor_id"] = actor_id
    return {
        "run_id": run_id,
        "workspace_id": "workspace-a",
        "status": "completed",
        "completed_at": completed_at,
        "trusted_identity": actor_id is not None,
        "actor": actor,
        "models": [
            {
                "agent": "df-coordinator",
                "deployment": "gpt-5-mini",
                "usage": {"input": 10, "output": 2, "total": 12},
            }
        ],
    }


def _summary(run: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": run["run_id"],
        "workspace_id": run["workspace_id"],
        "status": run["status"],
        "completed_at": run["completed_at"],
    }


def test_actor_ref_repair_defaults_to_dry_run_without_writing() -> None:
    run = _run("run-private-a", "2026-07-10T01:00:00Z")
    writes: list[dict[str, object]] = []

    result = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda value: writes.append(dict(value)) or 1,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        snapshot_secret=_SNAPSHOT_SECRET,
    )

    snapshot_token = result.pop("snapshot_token")
    assert str(snapshot_token).startswith("snap_v1_")
    assert result == {
        "status": "completed",
        "mode": "dry_run",
        "window": {
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-08-01T00:00:00Z",
        },
        "page_size": 100,
        "max_pages": 1,
        "pages_scanned": 1,
        "runs_scanned": 1,
        "runs_eligible": 1,
        "events_planned": 1,
        "events_applied": 0,
        "skipped": {},
        "next_cursor": None,
        "has_more": False,
    }
    assert writes == []


def test_actor_ref_repair_apply_is_idempotent_by_request_ref(monkeypatch) -> None:
    run = _run("run-private-a", "2026-07-10T01:00:00Z")
    repository = InMemoryFinOpsRepository()
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "1")

    def write(value: dict[str, object]) -> int:
        result = ingest_completed_run(
            value,
            repository=repository,
            hmac_secret="repair-secret",
        )
        return int(result["events"])

    dry_run = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=write,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        snapshot_secret=_SNAPSHOT_SECRET,
    )
    first = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=write,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        apply=True,
        snapshot_secret=_SNAPSHOT_SECRET,
        snapshot_token=str(dry_run["snapshot_token"]),
    )
    second = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=write,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        apply=True,
        snapshot_secret=_SNAPSHOT_SECRET,
        snapshot_token=str(dry_run["snapshot_token"]),
    )
    tenant_ref = canonical_tenant_ref(
        "TENANT-A",
        secret="repair-secret",
    )
    rows = repository.list_events(
        tenant_ref=tenant_ref,
        workspace_ids=("workspace-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
    )

    assert first["events_applied"] == second["events_applied"] == 1
    assert len(rows) == 1
    assert rows[0].request_ref.startswith("req_")
    assert rows[0].actor_ref == opaque_ref(
        "actor",
        "tenant-a",
        "member-a",
        secret="repair-secret",
    )


def test_actor_ref_repair_pages_are_bounded_and_resume_with_safe_cursor() -> None:
    runs = {
        "run-private-c": _run("run-private-c", "2026-07-03T01:00:00Z"),
        "run-private-a": _run("run-private-a", "2026-07-01T01:00:00Z"),
        "run-private-b": _run("run-private-b", "2026-07-02T01:00:00Z"),
    }
    loaded: list[str] = []

    first = repair_completed_run_actor_refs(
        run_summaries=[_summary(run) for run in runs.values()],
        run_loader=lambda run_id: loaded.append(run_id) or runs[run_id],
        event_writer=lambda _run: 1,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-10T00:00:00Z",
        page_size=1,
        max_pages=2,
        snapshot_secret=_SNAPSHOT_SECRET,
    )

    assert loaded == ["run-private-a", "run-private-b"]
    assert first["runs_scanned"] == 2
    assert first["events_planned"] == 2
    assert first["has_more"] is True
    assert str(first["next_cursor"]).startswith("cur_v1_")

    loaded.clear()
    second = repair_completed_run_actor_refs(
        run_summaries=[_summary(run) for run in runs.values()],
        run_loader=lambda run_id: loaded.append(run_id) or runs[run_id],
        event_writer=lambda _run: 1,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-10T00:00:00Z",
        cursor=str(first["next_cursor"]),
        page_size=1,
        max_pages=2,
        snapshot_secret=_SNAPSHOT_SECRET,
    )

    assert loaded == ["run-private-c"]
    assert second["runs_scanned"] == 1
    assert second["next_cursor"] is None
    assert second["has_more"] is False


@pytest.mark.parametrize(
    ("overrides", "category"),
    [
        ({"from_value": "2026-01-01T00:00:00Z"}, "window_exceeds_limit"),
        ({"page_size": 0}, "page_size_out_of_range"),
        ({"page_size": 201}, "page_size_out_of_range"),
        ({"max_pages": 0}, "max_pages_out_of_range"),
        ({"max_pages": 21}, "max_pages_out_of_range"),
        ({"cursor": "run-private-a"}, "invalid_cursor"),
    ],
)
def test_actor_ref_repair_rejects_unbounded_or_identity_bearing_inputs(
    overrides: dict[str, object],
    category: str,
) -> None:
    arguments: dict[str, object] = {
        "run_summaries": [],
        "run_loader": lambda _run_id: {},
        "event_writer": lambda _run: 0,
        "from_value": "2026-07-01T00:00:00Z",
        "to_value": "2026-08-01T00:00:00Z",
        "snapshot_secret": _SNAPSHOT_SECRET,
    }
    arguments.update(overrides)

    with pytest.raises(ActorRefRepairInputError) as error:
        repair_completed_run_actor_refs(**arguments)

    assert error.value.category == category


def test_actor_ref_repair_skips_nonterminal_and_overlarge_runs() -> None:
    running = _run("run-private-running", "2026-07-01T01:00:00Z")
    running["status"] = "running"
    overlarge = _run("run-private-large", "2026-07-02T01:00:00Z")
    overlarge["models"] = [{} for _ in range(65)]

    result = repair_completed_run_actor_refs(
        run_summaries=[_summary(running), _summary(overlarge)],
        run_loader=lambda run_id: {
            "run-private-running": running,
            "run-private-large": overlarge,
        }[run_id],
        event_writer=lambda _run: 0,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-10T00:00:00Z",
        snapshot_secret=_SNAPSHOT_SECRET,
    )

    assert result["runs_scanned"] == 1
    assert result["runs_eligible"] == 0
    assert result["events_planned"] == 0
    assert result["skipped"] == {"model_limit": 1}


def test_actor_ref_repair_rejects_detail_changed_after_summary_snapshot() -> None:
    summarized = _run("run-private-changed", "2026-07-02T01:00:00Z")
    changed = dict(summarized)
    changed["completed_at"] = "2026-07-02T01:05:00Z"

    result = repair_completed_run_actor_refs(
        run_summaries=[_summary(summarized)],
        run_loader=lambda _run_id: changed,
        event_writer=lambda _run: 1,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-10T00:00:00Z",
        snapshot_secret=_SNAPSHOT_SECRET,
    )

    assert result["events_applied"] == 0
    assert result["skipped"] == {"invalid_record": 1}


def test_actor_ref_repair_output_never_contains_raw_identity_or_run_references() -> None:
    run = _run("run-private-sensitive", "2026-07-10T01:00:00Z")

    result = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda _run: 1,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        snapshot_secret=_SNAPSHOT_SECRET,
    )
    serialized = json.dumps(result, sort_keys=True)

    for forbidden in (
        "run-private-sensitive",
        "TENANT-A",
        "MEMBER-A",
        "member-a@example.test",
        "actor_",
        "req_",
    ):
        assert forbidden not in serialized


def test_cli_requires_exact_confirmation_for_apply_and_defaults_to_dry_run() -> None:
    run = _run("run-private-a", "2026-07-10T01:00:00Z")
    outputs: list[str] = []
    writes: list[dict[str, object]] = []
    common = [
        "--from",
        "2026-07-01T00:00:00Z",
        "--to",
        "2026-08-01T00:00:00Z",
    ]

    dry_run_exit = main(
        common,
        summary_loader=lambda: [_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda value: writes.append(dict(value)) or 1,
        snapshot_secret=_SNAPSHOT_SECRET,
        output=outputs.append,
    )
    rejected_exit = main(
        [*common, "--apply"],
        summary_loader=lambda: [_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda value: writes.append(dict(value)) or 1,
        snapshot_secret=_SNAPSHOT_SECRET,
        output=outputs.append,
    )

    assert dry_run_exit == 0
    assert json.loads(outputs[0])["mode"] == "dry_run"
    assert rejected_exit == 2
    assert json.loads(outputs[1]) == {
        "status": "failed",
        "category": "confirmation_required",
    }
    assert writes == []


def test_cli_apply_emits_only_safe_aggregate_output() -> None:
    run = _run("run-private-a", "2026-07-10T01:00:00Z")
    outputs: list[str] = []
    common = [
        "--from",
        "2026-07-01T00:00:00Z",
        "--to",
        "2026-08-01T00:00:00Z",
    ]
    assert main(
        common,
        summary_loader=lambda: [_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda _run: 1,
        snapshot_secret=_SNAPSHOT_SECRET,
        output=outputs.append,
    ) == 0
    snapshot_token = str(json.loads(outputs.pop())["snapshot_token"])

    exit_code = main(
        [
            *common,
            "--apply",
            "--confirm",
            "APPLY_CANONICAL_ACTOR_REF_REPAIR",
            "--snapshot-token",
            snapshot_token,
        ],
        summary_loader=lambda: [_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda _run: 1,
        snapshot_secret=_SNAPSHOT_SECRET,
        output=outputs.append,
    )

    assert exit_code == 0
    payload = json.loads(outputs[0])
    assert payload["mode"] == "apply"
    assert payload["events_applied"] == 1
    assert "run-private-a" not in outputs[0]
    assert "TENANT-A" not in outputs[0]
    assert "MEMBER-A" not in outputs[0]


def test_apply_requires_matching_hmac_content_snapshot_before_any_write() -> None:
    run = _run("run-private-snapshot", "2026-07-10T01:00:00Z")
    common = {
        "run_summaries": [_summary(run)],
        "run_loader": lambda _run_id: run,
        "event_writer": lambda _run: 1,
        "from_value": "2026-07-01T00:00:00Z",
        "to_value": "2026-08-01T00:00:00Z",
        "snapshot_secret": "snapshot-secret",
    }

    dry_run = repair_completed_run_actor_refs(**common)

    assert str(dry_run["snapshot_token"]).startswith("snap_v1_")
    with pytest.raises(ActorRefRepairInputError) as missing:
        repair_completed_run_actor_refs(**common, apply=True)
    assert missing.value.category == "snapshot_required"

    applied = repair_completed_run_actor_refs(
        **common,
        apply=True,
        snapshot_token=str(dry_run["snapshot_token"]),
    )
    assert applied["events_applied"] == 1


def test_apply_fails_closed_when_any_model_content_changes_after_dry_run() -> None:
    run = _run("run-private-mutated", "2026-07-10T01:00:00Z")
    writes: list[dict[str, object]] = []

    dry_run = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda value: writes.append(dict(value)) or 1,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        snapshot_secret="snapshot-secret",
    )
    run["models"][0]["usage"]["total"] = 13  # type: ignore[index]

    with pytest.raises(ActorRefRepairInputError) as mismatch:
        repair_completed_run_actor_refs(
            run_summaries=[_summary(run)],
            run_loader=lambda _run_id: run,
            event_writer=lambda value: writes.append(dict(value)) or 1,
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-08-01T00:00:00Z",
            snapshot_secret="snapshot-secret",
            snapshot_token=str(dry_run["snapshot_token"]),
            apply=True,
        )

    assert mismatch.value.category == "snapshot_mismatch"
    assert writes == []


def test_signed_keyset_cursor_fails_closed_on_candidate_list_drift() -> None:
    first = _run("run-private-a", "2026-07-01T01:00:00Z")
    second = _run("run-private-b", "2026-07-02T01:00:00Z")
    runs = {
        str(first["run_id"]): first,
        str(second["run_id"]): second,
    }
    dry_run = repair_completed_run_actor_refs(
        run_summaries=[_summary(first), _summary(second)],
        run_loader=lambda run_id: runs[run_id],
        event_writer=lambda _run: 1,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-10T00:00:00Z",
        page_size=1,
        snapshot_secret="snapshot-secret",
    )

    assert str(dry_run["next_cursor"]).startswith("cur_v1_")
    assert "run-private" not in str(dry_run["next_cursor"])
    inserted = _run("run-private-inserted", "2026-07-01T12:00:00Z")
    runs[str(inserted["run_id"])] = inserted

    with pytest.raises(ActorRefRepairInputError) as drift:
        repair_completed_run_actor_refs(
            run_summaries=[
                _summary(first),
                _summary(inserted),
                _summary(second),
            ],
            run_loader=lambda run_id: runs[run_id],
            event_writer=lambda _run: 1,
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-10T00:00:00Z",
            cursor=str(dry_run["next_cursor"]),
            page_size=1,
            snapshot_secret="snapshot-secret",
        )

    assert drift.value.category == "source_snapshot_changed"


def test_legacy_tenant_keys_are_atomically_rekeyed_without_repricing() -> None:
    run = _run("run-private-priced", "2026-07-10T01:00:00Z")
    run["actor"]["tenant_id"] = "  TENANT-A  "  # type: ignore[index]
    plans = build_event_key_repairs(run, hmac_secret="repair-secret")
    [plan] = plans
    assert plan.legacy_tenant_ref != plan.canonical_event.tenant_ref
    assert plan.canonical_event.tenant_ref == canonical_tenant_ref(
        "tenant-a",
        secret="repair-secret",
    )
    legacy_event = normalize_run_event(
        run,
        model_index=0,
        tenant_id=plan.legacy_tenant_ref,
        raw_tenant_id="  TENANT-A  ",
        hmac_secret="repair-secret",
    ).model_copy(
        update={
            "request_ref": plan.legacy_request_ref,
            "estimated_cost": EstimatedCost.model_validate({
                "amount": 0.0042,
                "currency": "USD",
                "status": "estimated",
                "price_card_revision": "official-2026-07-01",
                "official_price_key": "azure-openai:gpt-5.1",
                "mapping_revision": 7,
            }),
        }
    )
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([legacy_event])

    assert repository.repair_event_keys(plans) == 1
    assert repository.get_event(
        tenant_ref=plan.legacy_tenant_ref,
        workspace_ids=("workspace-a",),
        request_ref=plan.legacy_request_ref,
    ) is None
    repaired = repository.get_event(
        tenant_ref=plan.canonical_event.tenant_ref,
        workspace_ids=("workspace-a",),
        request_ref=plan.canonical_event.request_ref,
    )
    assert repaired is not None
    assert repaired.estimated_cost == legacy_event.estimated_cost
    assert repaired.actor_ref == plan.canonical_event.actor_ref
    assert repository.repair_event_keys(plans) == 0


def test_rekey_preserves_historical_null_routing_revision_without_synthesis() -> None:
    run = _run("run-private-routing", "2026-07-10T01:00:00Z")
    run["actor"]["tenant_id"] = "TENANT-A"  # type: ignore[index]
    run["models"][0]["policy_revision"] = 12  # type: ignore[index]
    [plan] = build_event_key_repairs(run, hmac_secret="repair-secret")
    assert plan.canonical_event.routing_policy_revision == 12
    legacy = plan.canonical_event.model_copy(
        update={
            "tenant_ref": plan.legacy_tenant_ref,
            "request_ref": plan.legacy_request_ref,
            "routing_policy_revision": None,
        }
    )
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([legacy])

    assert repository.repair_event_keys([plan]) == 1
    repaired = repository.get_event(
        tenant_ref=plan.canonical_event.tenant_ref,
        workspace_ids=("workspace-a",),
        request_ref=plan.canonical_event.request_ref,
    )

    assert repaired is not None
    assert repaired.routing_policy_revision is None


def test_conflicting_canonical_price_evidence_rolls_back_entire_rekey_batch() -> None:
    first = _run("run-private-first", "2026-07-10T01:00:00Z")
    second = _run("run-private-second", "2026-07-10T02:00:00Z")
    first["actor"]["tenant_id"] = "TENANT-A"  # type: ignore[index]
    second["actor"]["tenant_id"] = "TENANT-A"  # type: ignore[index]
    plans = [
        *build_event_key_repairs(first, hmac_secret="repair-secret"),
        *build_event_key_repairs(second, hmac_secret="repair-secret"),
    ]
    repository = InMemoryFinOpsRepository()
    legacy_events = [
        normalize_run_event(
            run,
            model_index=0,
            tenant_id=plan.legacy_tenant_ref,
            raw_tenant_id="TENANT-A",
            hmac_secret="repair-secret",
        ).model_copy(
            update={
                "request_ref": plan.legacy_request_ref,
                "estimated_cost": EstimatedCost.model_validate({
                    "amount": amount,
                    "currency": "USD",
                    "status": "estimated",
                    "price_card_revision": "price-original",
                }),
            }
        )
        for run, plan, amount in (
            (first, plans[0], 0.001),
            (second, plans[1], 0.002),
        )
    ]
    conflicting_canonical = plans[1].canonical_event.model_copy(
        update={
            "estimated_cost": EstimatedCost.model_validate({
                "amount": 9.999,
                "currency": "USD",
                "status": "estimated",
                "price_card_revision": "price-conflict",
            })
        }
    )
    repository.upsert_events([*legacy_events, conflicting_canonical])

    with pytest.raises(FinOpsEventRepairConflict):
        repository.repair_event_keys(plans)

    assert repository.get_event(
        tenant_ref=plans[0].legacy_tenant_ref,
        workspace_ids=("workspace-a",),
        request_ref=plans[0].legacy_request_ref,
    ) == legacy_events[0]
    assert repository.get_event(
        tenant_ref=plans[0].canonical_event.tenant_ref,
        workspace_ids=("workspace-a",),
        request_ref=plans[0].canonical_event.request_ref,
    ) is None
