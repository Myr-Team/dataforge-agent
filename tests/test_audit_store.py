import base64
import json
import secrets

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
    monkeypatch.setenv("DF_AUDIT_LOCAL_MODE", "1")
    monkeypatch.setenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", "test-v1")
    monkeypatch.setenv(
        "DF_AUDIT_HMAC_KEYS",
        json.dumps({"test-v1": base64.b64encode(secrets.token_bytes(32)).decode("ascii")}),
    )


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
    monkeypatch.delenv("DF_ROI_PSEUDONYM_SALT", raising=False)
    monkeypatch.delenv("DF_AUDIT_HMAC_KEYS", raising=False)
    monkeypatch.delenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", raising=False)

    first = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    second = audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    key_path = audit_store.AUDIT_DIR / ".keyring.json"
    assert key_path.exists()
    assert len(key_path.read_bytes()) >= 32
    assert first["actor_hash"] == second["actor_hash"]
    assert b"owner@contoso.com" not in key_path.read_bytes()


def test_blob_store_requires_deployment_hmac_key(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "blob_configured", lambda: True)
    monkeypatch.setattr(audit_store, "_backend", lambda: audit_store._LocalAppendBackend())
    monkeypatch.delenv("DF_AUDIT_HMAC_KEYS", raising=False)
    monkeypatch.delenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", raising=False)

    with pytest.raises(audit_store.AuditPersistenceError, match="DF_AUDIT_HMAC_KEYS"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})


def test_audit_event_cannot_be_updated_or_deleted() -> None:
    audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    assert not hasattr(audit_store, "update_audit_event")
    assert not hasattr(audit_store, "delete_audit_event")


def test_audit_event_chain_records_each_attempt_even_with_reused_request_id() -> None:
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

    assert replay["event_id"] != first["event_id"]
    assert page["revision"] == 3
    assert [item["event_id"] for item in page["events"]] == [second["event_id"], replay["event_id"], first["event_id"]]
    assert first["revision"] == 1
    assert first["previous_hash"] == audit_store.GENESIS_HASH
    assert replay["revision"] == 2
    assert replay["previous_hash"] == first["event_hash"]
    assert second["revision"] == 3
    assert second["previous_hash"] == replay["event_hash"]
    assert second["event_hash"] != first["event_hash"]


def test_remediation_local_storage_requires_explicit_flag(monkeypatch) -> None:
    monkeypatch.delenv("DF_AUDIT_LOCAL_MODE", raising=False)

    with pytest.raises(audit_store.AuditPersistenceError, match="explicit"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})


def test_remediation_storage_account_name_is_recognized(monkeypatch) -> None:
    monkeypatch.undo()
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("DF_STORAGE_ACCOUNT", raising=False)
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", "deployedaccount")

    from backend import blob_store

    assert blob_store.blob_configured() is True


def test_remediation_key_ring_rotates_and_validates_old_events(monkeypatch) -> None:
    old = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    new = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", "old")
    monkeypatch.setenv("DF_AUDIT_HMAC_KEYS", json.dumps({"old": old}))
    first = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    monkeypatch.setenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", "new")
    monkeypatch.setenv("DF_AUDIT_HMAC_KEYS", json.dumps({"new": new, "old": old}))
    second = audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})
    listed = audit_store.list_audit_events("ws-audit")

    assert first["key_id"] == "old"
    assert second["key_id"] == "new"
    assert listed["revision"] == 2


def test_remediation_rejects_short_decoded_hmac_key(monkeypatch) -> None:
    monkeypatch.setenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", "short")
    monkeypatch.setenv(
        "DF_AUDIT_HMAC_KEYS",
        json.dumps({"short": base64.b64encode(b"too-short").decode("ascii")}),
    )

    with pytest.raises(audit_store.AuditPersistenceError, match="256-bit"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})


@pytest.mark.parametrize("workspace_id", ["../escape", "a/b", "a\\b", ".", "..", "C:drive"])
def test_remediation_rejects_workspace_path_traversal(workspace_id: str) -> None:
    with pytest.raises(ValueError, match="workspace_id"):
        audit_store.list_audit_events(workspace_id)


def test_remediation_local_anchor_detects_ledger_delete_and_rollback() -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})
    event_path = audit_store._local_stream_path(audit_store._event_stream_name("ws-audit", 1))
    original = event_path.read_bytes()
    first_line = original.splitlines(keepends=True)[0]

    event_path.write_bytes(first_line)
    with pytest.raises(audit_store.AuditIntegrityError, match="rollback"):
        audit_store.list_audit_events("ws-audit")

    event_path.unlink()
    with pytest.raises(audit_store.AuditIntegrityError, match="missing"):
        audit_store.list_audit_events("ws-audit")


def test_remediation_valid_unanchored_event_is_recovered_after_interrupted_writer(monkeypatch) -> None:
    class InterruptedBackend:
        def __init__(self) -> None:
            self.streams: dict[str, bytes] = {}

        def read(self, name: str) -> bytes:
            return self.streams.get(name, b"")

        def list_names(self, prefix: str) -> list[str]:
            return sorted(name for name in self.streams if name.startswith(prefix))

        def append(self, name: str, payload: bytes, expected_size: int) -> None:
            current = self.streams.get(name, b"")
            if len(current) != expected_size:
                raise audit_store._AppendConflict()
            self.streams[name] = current + payload

    remote = InterruptedBackend()
    first = audit_store._build_event(
        _actor(),
        "file.create",
        _resource(),
        {},
        revision=1,
        previous_hash=audit_store.GENESIS_HASH,
    )
    interrupted = audit_store._build_event(
        _actor(),
        "file.edit",
        _resource(),
        {},
        revision=2,
        previous_hash=first["event_hash"],
    )
    anchor = audit_store._build_anchor(
        anchor_revision=1,
        workspace_id="ws-audit",
        workspace_revision=1,
        workspace_event_hash=first["event_hash"],
        previous_hash=audit_store.GENESIS_HASH,
        key_id=first["key_id"],
    )
    remote.streams[audit_store._event_stream_name("ws-audit", 1)] = (
        audit_store._line(first) + audit_store._line(interrupted)
    )
    remote.streams[audit_store._anchor_stream_name(1)] = audit_store._line(anchor)
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    third = audit_store.record_audit_event(_actor(), "file.delete", _resource(), {})

    assert third["revision"] == 3
    listed = audit_store.list_audit_events("ws-audit")["events"]
    assert [event["revision"] for event in listed] == [3, 2, 1]


@pytest.mark.parametrize("name", ["/outside.jsonl", "\\outside.jsonl", "C:\\outside.jsonl", "\\\\server\\share\\x.jsonl"])
def test_remediation_local_stream_rejects_absolute_paths(name: str) -> None:
    with pytest.raises(ValueError, match="path"):
        audit_store._local_stream_path(name)


def test_remediation_production_contract_requires_all_blob_controls(monkeypatch) -> None:
    service = {
        "isVersioningEnabled": True,
        "deleteRetentionPolicy": {"enabled": True, "days": 30},
        "containerDeleteRetentionPolicy": {"enabled": True, "days": 30},
    }
    policy = {"state": "Locked", "allowProtectedAppendWrites": True}
    audit_store._validate_production_contract(service, policy)

    for broken_service, broken_policy in [
        ({**service, "isVersioningEnabled": False}, policy),
        ({**service, "deleteRetentionPolicy": {"enabled": False}}, policy),
        ({**service, "containerDeleteRetentionPolicy": {"enabled": False}}, policy),
        (service, {**policy, "state": "Unlocked"}),
        (service, {**policy, "allowProtectedAppendWrites": False}),
    ]:
        with pytest.raises(audit_store.AuditPersistenceError, match="contract"):
            audit_store._validate_production_contract(broken_service, broken_policy)


def test_remediation_production_never_falls_back_to_local(monkeypatch) -> None:
    monkeypatch.setenv("DF_ENVIRONMENT", "production")
    monkeypatch.setenv("DF_AUDIT_LOCAL_MODE", "1")
    monkeypatch.setattr(audit_store, "blob_configured", lambda: False)

    with pytest.raises(audit_store.AuditPersistenceError, match="prohibited"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})


def test_remediation_infra_preauthorizes_versionless_key_vault_reference() -> None:
    module = (audit_store.ROOT / "infra" / "modules" / "container_app" / "main.tf").read_text(encoding="utf-8")
    variables = (audit_store.ROOT / "infra" / "envs" / "dev" / "variables.tf").read_text(encoding="utf-8")

    assert 'resource "azurerm_user_assigned_identity" "audit_secrets"' in module
    assert 'role_definition_name = "Key Vault Secrets User"' in module
    assert "identity            = azurerm_user_assigned_identity.audit_secrets.id" in module
    assert "depends_on = [azurerm_role_assignment.audit_secrets_user]" in module
    assert "audit_key_vault_id" in variables
    assert "audit_hmac_keyring_secret_uri" in variables
    assert "validation" in variables
    assert "/secrets/[^/]+$" in variables


def test_remediation_production_requires_configured_durable_blob(monkeypatch) -> None:
    monkeypatch.setenv("DF_ENVIRONMENT", "production")
    monkeypatch.delenv("DF_AUDIT_LOCAL_MODE", raising=False)
    monkeypatch.setattr(audit_store, "blob_configured", lambda: False)

    with pytest.raises(audit_store.AuditPersistenceError, match="durable Blob"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})


def test_tampered_local_chain_fails_closed(tmp_path) -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    ledger_path = audit_store._local_stream_path(audit_store._event_stream_name("ws-audit", 1))
    event = json.loads(ledger_path.read_text(encoding="utf-8"))
    event["resource_id"] = "tampered"
    ledger_path.write_bytes(audit_store._line(event))

    with pytest.raises(audit_store.AuditIntegrityError):
        audit_store.list_audit_events("ws-audit")
    with pytest.raises(audit_store.AuditIntegrityError):
        audit_store.record_audit_event(_actor(), "file.delete", _resource(), {})

    assert json.loads(ledger_path.read_text(encoding="utf-8"))["resource_id"] == "tampered"


def test_local_append_lock_failure_is_fail_closed(monkeypatch) -> None:
    audit_store.AUDIT_DIR.mkdir(parents=True)
    lock_path = audit_store.AUDIT_DIR / ".ledger.lock"
    lock_path.write_text("held-by-another-process", encoding="ascii")
    monkeypatch.setattr(audit_store, "LOCAL_LOCK_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(audit_store.AuditPersistenceError, match="lock"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    assert not audit_store._local_stream_path(audit_store._event_stream_name("ws-audit", 1)).exists()


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
    ledger_path = audit_store._local_stream_path(audit_store._event_stream_name("ws-audit", 1))
    event = json.loads(ledger_path.read_text(encoding="utf-8"))
    event[field] = value
    event["event_hash"] = audit_store._hash_event(event)
    ledger_path.write_bytes(audit_store._line(event))

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
    class ReplicaBackend:
        def __init__(self) -> None:
            self.streams: dict[str, bytes] = {}
            self.event_calls: list[int] = []

        def read(self, name: str) -> bytes:
            return self.streams.get(name, b"")

        def list_names(self, prefix: str) -> list[str]:
            return sorted(name for name in self.streams if name.startswith(prefix))

        def append(self, name: str, payload: bytes, expected_size: int) -> None:
            if "/events/" in name:
                self.event_calls.append(expected_size)
                if len(self.event_calls) == 1:
                    competing = audit_store._build_event(
                        _actor(),
                        "message.create",
                        {"workspace_id": "ws-audit", "resource_type": "message", "resource_id": "message-other"},
                        {"correlation": {"request_id": "req-other"}},
                        revision=1,
                        previous_hash=audit_store.GENESIS_HASH,
                    )
                    self.streams[name] = audit_store._line(competing)
                    anchor = audit_store._build_anchor(
                        anchor_revision=1,
                        workspace_id="ws-audit",
                        workspace_revision=1,
                        workspace_event_hash=competing["event_hash"],
                        previous_hash=audit_store.GENESIS_HASH,
                        key_id=competing["key_id"],
                    )
                    self.streams[audit_store._anchor_stream_name(1)] = audit_store._line(anchor)
                    raise audit_store._AppendConflict()
            current = self.streams.get(name, b"")
            if len(current) != expected_size:
                raise audit_store._AppendConflict()
            self.streams[name] = current + payload

    remote = ReplicaBackend()
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    event = audit_store.record_audit_event(
        _actor(),
        "file.create",
        _resource(),
        {"correlation": {"request_id": "req-local"}},
    )

    assert remote.event_calls[0] == 0
    assert remote.event_calls[1] > 0
    assert event["revision"] == 2
    persisted = audit_store.list_audit_events("ws-audit")["events"]
    assert event["previous_hash"] == persisted[1]["event_hash"]
    assert len(persisted) == 2


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
