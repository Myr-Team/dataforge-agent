from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.finops.actor_ref_repair import (
    ActorRefRepairInputError,
    main,
    repair_completed_run_actor_refs,
)
from backend.finops.ingestion import ingest_completed_run
from backend.finops.normalization import opaque_ref
from backend.finops.repository import InMemoryFinOpsRepository


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
    )

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

    first = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=write,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        apply=True,
    )
    second = repair_completed_run_actor_refs(
        run_summaries=[_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=write,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        apply=True,
    )
    tenant_ref = opaque_ref(
        "tenant",
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
    )

    assert loaded == ["run-private-a", "run-private-b"]
    assert first["runs_scanned"] == 2
    assert first["events_planned"] == 2
    assert first["has_more"] is True
    assert first["next_cursor"] == "offset_00000002"

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
        apply=True,
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
        output=outputs.append,
    )
    rejected_exit = main(
        [*common, "--apply"],
        summary_loader=lambda: [_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda value: writes.append(dict(value)) or 1,
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

    exit_code = main(
        [
            "--from",
            "2026-07-01T00:00:00Z",
            "--to",
            "2026-08-01T00:00:00Z",
            "--apply",
            "--confirm",
            "APPLY_CANONICAL_ACTOR_REF_REPAIR",
        ],
        summary_loader=lambda: [_summary(run)],
        run_loader=lambda _run_id: run,
        event_writer=lambda _run: 1,
        output=outputs.append,
    )

    assert exit_code == 0
    payload = json.loads(outputs[0])
    assert payload["mode"] == "apply"
    assert payload["events_applied"] == 1
    assert "run-private-a" not in outputs[0]
    assert "TENANT-A" not in outputs[0]
    assert "MEMBER-A" not in outputs[0]
