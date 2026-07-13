import json

import pytest

import backend.audit_store as audit_store


def _actor() -> dict[str, str]:
    return {
        "actor_id": "oid-owner-123",
        "tenant_id": "tenant-456",
        "email": "owner@contoso.com",
        "source": "easy_auth",
    }


def _resource(resource_id: str = "file-1") -> dict[str, str]:
    return {"workspace_id": "ws-audit", "resource_type": "file", "resource_id": resource_id}


@pytest.fixture(autouse=True)
def _local_audit_store(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(audit_store, "blob_configured", lambda: False)
    monkeypatch.setenv("DF_AUDIT_HMAC_KEY", "unit-test-audit-key")


def test_audit_event_redacts_content_credentials_and_email() -> None:
    event = audit_store.record_audit_event(
        _actor(),
        "file.edit",
        _resource(),
        {
            "result": "allowed",
            "reason_code": "authorized",
            "correlation": {
                "request_id": "req-123",
                "run_id": "run-456",
                "provider_body": "drop-this",
                "email": "target@contoso.com",
            },
            "raw_prompt": "summarize confidential roadmap",
            "password": "Password=secret",
            "connection_string": "AccountKey=secret",
            "file_content": "private rows",
        },
    )

    text = json.dumps(event, sort_keys=True)
    assert "Password=" not in text
    assert "@contoso" not in text
    assert "raw_prompt" not in text
    assert "provider_body" not in text
    assert "connection_string" not in text
    assert "file_content" not in text
    assert event["actor_hash"].startswith("actor_")
    assert event["correlation"] == {"request_id": "req-123", "run_id": "run-456"}


def test_local_store_creates_and_reuses_private_hmac_key(monkeypatch) -> None:
    monkeypatch.delenv("DF_AUDIT_HMAC_KEY", raising=False)
    monkeypatch.delenv("DF_ROI_PSEUDONYM_SALT", raising=False)

    first = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    second = audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    key_path = audit_store.AUDIT_DIR / ".hmac-key"
    assert key_path.exists()
    assert len(key_path.read_bytes()) >= 32
    assert first["actor_hash"] == second["actor_hash"]
    assert b"dataforge-local-audit-hmac-v1" not in key_path.read_bytes()


def test_blob_store_requires_deployment_hmac_key(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "blob_configured", lambda: True)
    monkeypatch.delenv("DF_AUDIT_HMAC_KEY", raising=False)
    monkeypatch.delenv("DF_ROI_PSEUDONYM_SALT", raising=False)

    with pytest.raises(audit_store.AuditPersistenceError, match="DF_AUDIT_HMAC_KEY"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})


def test_audit_event_cannot_be_updated_or_deleted() -> None:
    audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    assert not hasattr(audit_store, "update_audit_event")
    assert not hasattr(audit_store, "delete_audit_event")


def test_audit_event_chain_is_append_only_and_idempotent() -> None:
    first = audit_store.record_audit_event(
        _actor(),
        "file.create",
        _resource("file-1"),
        {"correlation": {"request_id": "req-stable"}},
    )
    replay = audit_store.record_audit_event(
        _actor(),
        "file.create",
        _resource("file-1"),
        {"correlation": {"request_id": "req-stable"}},
    )
    second = audit_store.record_audit_event(
        _actor(),
        "file.delete",
        _resource("file-1"),
        {"correlation": {"request_id": "req-delete"}},
    )
    page = audit_store.list_audit_events("ws-audit", limit=10)

    assert replay == first
    assert page["revision"] == 2
    assert [item["event_id"] for item in page["events"]] == [second["event_id"], first["event_id"]]
    assert first["revision"] == 1
    assert first["previous_hash"] == audit_store.GENESIS_HASH
    assert second["revision"] == 2
    assert second["previous_hash"] == first["event_hash"]
    assert second["event_hash"] != first["event_hash"]


def test_tampered_local_chain_fails_closed(tmp_path) -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    ledger_path = audit_store.AUDIT_DIR / "ws-audit.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["events"][0]["resource_id"] = "tampered"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(audit_store.AuditIntegrityError):
        audit_store.list_audit_events("ws-audit")
    with pytest.raises(audit_store.AuditIntegrityError):
        audit_store.record_audit_event(_actor(), "file.delete", _resource(), {})

    assert json.loads(ledger_path.read_text(encoding="utf-8"))["events"][0]["resource_id"] == "tampered"


def test_local_append_lock_failure_is_fail_closed(monkeypatch) -> None:
    audit_store.AUDIT_DIR.mkdir(parents=True)
    lock_path = audit_store.AUDIT_DIR / "ws-audit.lock"
    lock_path.write_text("held-by-another-process", encoding="ascii")
    monkeypatch.setattr(audit_store, "LOCAL_LOCK_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(audit_store.AuditPersistenceError, match="lock"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    assert not (audit_store.AUDIT_DIR / "ws-audit.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action", "forged.action"),
        ("actor_hash", "owner@contoso.com"),
        ("at", "2026-07-14T10:00:00"),
    ],
)
def test_rehashed_policy_invalid_event_is_rejected(field: str, value: str) -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    ledger_path = audit_store.AUDIT_DIR / "ws-audit.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["events"][0][field] = value
    ledger["events"][0]["event_hash"] = audit_store._hash_event(ledger["events"][0])
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(audit_store.AuditIntegrityError):
        audit_store.list_audit_events("ws-audit")


def test_pagination_is_bounded_and_uses_opaque_revision_cursor() -> None:
    for index in range(4):
        audit_store.record_audit_event(
            _actor(),
            "file.edit",
            _resource(f"file-{index}"),
            {"correlation": {"request_id": f"req-{index}"}},
        )

    first = audit_store.list_audit_events("ws-audit", limit=2)
    second = audit_store.list_audit_events("ws-audit", limit=2, cursor=first["next_cursor"])

    assert len(first["events"]) == 2
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert len(second["events"]) == 2
    assert not ({event["event_id"] for event in first["events"]} & {event["event_id"] for event in second["events"]})
    with pytest.raises(ValueError):
        audit_store.list_audit_events("ws-audit", limit=audit_store.MAX_PAGE_SIZE + 1)


def test_blob_cas_retries_replica_conflict_without_losing_events(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "blob_configured", lambda: True)
    remote: dict[str, object] = {}
    calls: list[int] = []

    def download(_name: str):
        return json.loads(json.dumps(remote)) if remote else None

    def cas(_name: str, *, expected_revision: int, changes: dict):
        calls.append(expected_revision)
        if len(calls) == 1:
            competing = audit_store._build_event(
                _actor(),
                "message.create",
                {"workspace_id": "ws-audit", "resource_type": "message", "resource_id": "message-other"},
                {"correlation": {"request_id": "req-other"}},
                revision=1,
                previous_hash=audit_store.GENESIS_HASH,
            )
            remote.update({"revision": 1, "events": [competing]})
            return None
        if int(remote.get("revision", 0)) != expected_revision:
            return None
        remote.update(json.loads(json.dumps(changes)))
        return json.loads(json.dumps(remote))

    monkeypatch.setattr(audit_store, "download_blob_json", download)
    monkeypatch.setattr(audit_store, "compare_and_swap_blob_json", cas)

    event = audit_store.record_audit_event(
        _actor(),
        "file.create",
        _resource(),
        {"correlation": {"request_id": "req-local"}},
    )

    assert calls == [0, 1]
    assert event["revision"] == 2
    assert event["previous_hash"] == remote["events"][0]["event_hash"]
    assert len(remote["events"]) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action", "arbitrary.action"),
        ("resource_type", "provider_body"),
        ("result", "success"),
        ("reason_code", "free form reason"),
    ],
)
def test_schema_fields_are_strictly_allowlisted(field: str, value: str) -> None:
    action = "file.edit"
    resource = _resource()
    metadata = {"result": "allowed", "reason_code": "authorized"}
    if field == "action":
        action = value
    elif field in resource:
        resource[field] = value
    else:
        metadata[field] = value

    with pytest.raises(ValueError):
        audit_store.record_audit_event(_actor(), action, resource, metadata)
