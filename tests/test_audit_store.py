import base64
import json
import re
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
    assert set(event["correlation"]) == {"request_id", "run_id"}
    assert all(re.fullmatch(r"corr_[0-9a-f]{40}", value) for value in event["correlation"].values())
    assert "req-123" not in text
    assert "run-456" not in text


def test_correlation_values_are_never_persisted_or_returned_raw() -> None:
    secrets_by_field = {
        "request_id": "eyJhbGciOiJIUzI1NiJ9.api-key-secret.signature",
        "run_id": "Server=tcp:db;Password=super-secret",
        "task_id": "sk-live-client-controlled-token",
    }

    audit_store.record_audit_event(
        _actor(),
        "file.edit",
        _resource(),
        {"correlation": secrets_by_field},
    )

    persisted = b"".join(path.read_bytes() for path in audit_store.AUDIT_DIR.rglob("*.jsonl"))
    response = json.dumps(audit_store.list_audit_events("ws-audit"), sort_keys=True)
    for secret in secrets_by_field.values():
        assert secret.encode("utf-8") not in persisted
        assert secret not in response
    assert all(
        re.fullmatch(r"corr_[0-9a-f]{40}", value)
        for value in audit_store.list_audit_events("ws-audit")["events"][0]["correlation"].values()
    )


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
            self.versions: dict[str, int] = {}

        def read_snapshot(self, name: str):
            data = self.streams.get(name, b"")
            return audit_store._StreamSnapshot(
                name=name,
                data=data[-audit_store.STREAM_TAIL_BYTES :],
                head=json.loads(data.splitlines()[-1]) if data else None,
                length=len(data),
                etag=str(self.versions.get(name)) if name in self.streams else None,
            )

        def read_full(self, name: str) -> bytes:
            return self.streams.get(name, b"")

        def append(self, name: str, payload: bytes, snapshot) -> None:
            current = self.streams.get(name, b"")
            current_etag = str(self.versions.get(name)) if name in self.streams else None
            if len(current) != snapshot.length or current_etag != snapshot.etag:
                raise audit_store._AppendConflict()
            self.streams[name] = current + payload
            self.versions[name] = self.versions.get(name, 0) + 1

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
    remote.streams[audit_store._anchor_stream_name("ws-audit")] = audit_store._line(anchor)
    remote.versions[audit_store._event_stream_name("ws-audit")] = 1
    remote.versions[audit_store._anchor_stream_name("ws-audit")] = 1
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    third = audit_store.record_audit_event(_actor(), "file.delete", _resource(), {})

    assert third["revision"] == 3
    listed = audit_store.list_audit_events("ws-audit")["events"]
    assert [event["revision"] for event in listed] == [3, 2, 1]


def test_first_event_is_recovered_when_anchor_append_was_interrupted(monkeypatch) -> None:
    original_append = audit_store._LocalAppendBackend.append
    interrupted = {"value": False}

    def fail_first_anchor(self, name, payload, expected):
        if "anchor" in name and not interrupted["value"]:
            interrupted["value"] = True
            raise audit_store.AuditPersistenceError("simulated interruption")
        return original_append(self, name, payload, expected)

    monkeypatch.setattr(audit_store._LocalAppendBackend, "append", fail_first_anchor)

    with pytest.raises(audit_store.AuditPersistenceError, match="interruption"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    second = audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    assert second["revision"] == 2
    assert [event["revision"] for event in audit_store.list_audit_events("ws-audit")["events"]] == [2, 1]


def test_first_event_for_another_workspace_is_recovered_after_anchor_interruption(monkeypatch) -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    original_append = audit_store._LocalAppendBackend.append
    interrupted = {"value": False}

    def fail_next_workspace_anchor(self, name, payload, expected):
        if "anchor" in name and "ws-other" in name and not interrupted["value"]:
            interrupted["value"] = True
            raise audit_store.AuditPersistenceError("simulated interruption")
        return original_append(self, name, payload, expected)

    monkeypatch.setattr(audit_store._LocalAppendBackend, "append", fail_next_workspace_anchor)
    other = {"workspace_id": "ws-other", "resource_type": "file", "resource_id": "file-2"}

    with pytest.raises(audit_store.AuditPersistenceError, match="interruption"):
        audit_store.record_audit_event(_actor(), "file.create", other, {})

    recovered = audit_store.record_audit_event(_actor(), "file.edit", other, {})
    assert recovered["revision"] == 2


def test_multiple_genesis_records_without_an_anchor_fail_closed(monkeypatch) -> None:
    remote = _SnapshotRaceBackend()
    name = audit_store._event_stream_name("ws-audit")
    first = audit_store._build_event(
        _actor(), "file.create", _resource("file-1"), {}, revision=1, previous_hash=audit_store.GENESIS_HASH
    )
    forged_second_genesis = audit_store._build_event(
        _actor(), "file.create", _resource("file-2"), {}, revision=1, previous_hash=audit_store.GENESIS_HASH
    )
    remote._commit(name, audit_store._line(first))
    remote._commit(name, audit_store._line(forged_second_genesis))
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    with pytest.raises(audit_store.AuditIntegrityError, match="anchor is missing"):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})


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


def _production_storage_env(monkeypatch, *, account: str = "writeaccount") -> str:
    resource_id = (
        "/subscriptions/sub-expected/resourceGroups/rg-expected/providers/"
        f"Microsoft.Storage/storageAccounts/{account}"
    )
    monkeypatch.setenv("DF_ENVIRONMENT", "production")
    monkeypatch.delenv("DF_AUDIT_LOCAL_MODE", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_KEY", raising=False)
    monkeypatch.delenv("DF_STORAGE_KEY", raising=False)
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", account)
    monkeypatch.setenv("DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID", resource_id)
    monkeypatch.setenv("DF_AUDIT_STORAGE_SUBSCRIPTION_ID", "sub-expected")
    monkeypatch.setenv("DF_AUDIT_STORAGE_RESOURCE_GROUP", "rg-expected")
    return resource_id


@pytest.mark.parametrize(
    ("resource_id", "message"),
    [
        (
            "/subscriptions/sub-expected/resourceGroups/rg-expected/providers/Microsoft.Storage/storageAccounts/verified-a",
            "account",
        ),
        (
            "/subscriptions/sub-other/resourceGroups/rg-expected/providers/Microsoft.Storage/storageAccounts/writeaccount",
            "subscription",
        ),
        (
            "/subscriptions/sub-expected/resourceGroups/rg-other/providers/Microsoft.Storage/storageAccounts/writeaccount",
            "resource group",
        ),
    ],
)
def test_production_worm_proof_is_bound_to_write_account_subscription_and_rg(monkeypatch, resource_id, message) -> None:
    _production_storage_env(monkeypatch)
    monkeypatch.setenv("DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID", resource_id)

    with pytest.raises(audit_store.AuditPersistenceError, match=message):
        audit_store._verify_production_storage_contract("writeaccount")


def test_verified_account_a_cannot_authorize_writes_to_account_b(monkeypatch) -> None:
    _production_storage_env(monkeypatch, account="writeaccountb")
    monkeypatch.setenv(
        "DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID",
        "/subscriptions/sub-expected/resourceGroups/rg-expected/providers/"
        "Microsoft.Storage/storageAccounts/verifiedaccounta",
    )
    constructed: list[dict] = []
    monkeypatch.setattr(audit_store, "_BlobAppendBackend", lambda **kwargs: constructed.append(kwargs))

    with pytest.raises(audit_store.AuditPersistenceError, match="account proof"):
        audit_store._backend()

    assert constructed == []


def test_production_backend_writes_to_the_exact_arm_verified_account_with_managed_identity(monkeypatch) -> None:
    resource_id = _production_storage_env(monkeypatch)
    verified: list[str] = []
    constructed: list[dict] = []
    sentinel = object()

    def verify(account_name):
        verified.append(account_name)
        return resource_id

    def backend(**kwargs):
        constructed.append(kwargs)
        return sentinel

    monkeypatch.setattr(audit_store, "_verify_production_storage_contract", verify)
    monkeypatch.setattr(audit_store, "_BlobAppendBackend", backend)

    assert audit_store._backend() is sentinel
    assert verified == ["writeaccount"]
    assert constructed == [{"account_name": "writeaccount", "managed_identity_only": True}]


def test_production_rejects_connection_strings_and_storage_keys(monkeypatch) -> None:
    _production_storage_env(monkeypatch)
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")

    with pytest.raises(audit_store.AuditPersistenceError, match="connection string"):
        audit_store._backend()

    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING")
    monkeypatch.setenv("AZURE_STORAGE_KEY", "not-allowed")
    with pytest.raises(audit_store.AuditPersistenceError, match="managed identity"):
        audit_store._backend()


def test_production_contract_cache_is_bounded_and_rechecks_after_expiry(monkeypatch) -> None:
    resource_id = _production_storage_env(monkeypatch)
    clock = {"now": 100.0}
    calls: list[str] = []
    broken = {"value": False}

    def management_get(path: str):
        calls.append(path)
        if "immutabilityPolicies" in path:
            return {
                "properties": {
                    "state": "Unlocked" if broken["value"] else "Locked",
                    "allowProtectedAppendWrites": True,
                }
            }
        return {
            "properties": {
                "isVersioningEnabled": True,
                "deleteRetentionPolicy": {"enabled": True, "days": 30},
                "containerDeleteRetentionPolicy": {"enabled": True, "days": 30},
            }
        }

    audit_store._PRODUCTION_CONTRACT_CACHE.clear()
    monkeypatch.setattr(audit_store.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(audit_store, "_management_get_json", management_get)

    assert audit_store._verify_production_storage_contract("writeaccount") == resource_id
    assert audit_store._verify_production_storage_contract("writeaccount") == resource_id
    assert len(calls) == 2

    broken["value"] = True
    clock["now"] += audit_store.PRODUCTION_CONTRACT_CACHE_TTL_SECONDS + 0.01
    with pytest.raises(audit_store.AuditPersistenceError, match="contract"):
        audit_store._verify_production_storage_contract("writeaccount")
    assert len(calls) == 4


def test_remediation_production_never_falls_back_to_local(monkeypatch) -> None:
    monkeypatch.setenv("DF_ENVIRONMENT", "production")
    monkeypatch.setenv("DF_AUDIT_LOCAL_MODE", "1")
    monkeypatch.setattr(audit_store, "blob_configured", lambda: False)

    with pytest.raises(audit_store.AuditPersistenceError, match="prohibited"):
        audit_store.record_audit_event(_actor(), "file.create", _resource(), {})


def test_remediation_infra_preauthorizes_versionless_key_vault_reference() -> None:
    module = (audit_store.ROOT / "infra" / "modules" / "container_app" / "main.tf").read_text(encoding="utf-8")
    environment = (audit_store.ROOT / "infra" / "envs" / "dev" / "main.tf").read_text(encoding="utf-8")
    variables = (audit_store.ROOT / "infra" / "envs" / "dev" / "variables.tf").read_text(encoding="utf-8")

    assert 'resource "azurerm_user_assigned_identity" "audit_secrets"' in module
    assert 'role_definition_name = "Key Vault Secrets User"' in module
    assert "identity            = azurerm_user_assigned_identity.audit_secrets.id" in module
    assert "depends_on = [azurerm_role_assignment.audit_secrets_user]" in module
    assert "audit_key_vault_id" in variables
    assert "audit_hmac_keyring_secret_uri" in variables
    assert "validation" in variables
    assert "/secrets/[^/]+$" in variables
    assert 'name  = "DF_AUDIT_STORAGE_SUBSCRIPTION_ID"' in module
    assert 'name  = "DF_AUDIT_STORAGE_RESOURCE_GROUP"' in module
    assert "subscription_id                  = var.subscription_id" in environment


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
            self.versions: dict[str, int] = {}
            self.event_calls: list[int] = []

        def read_snapshot(self, name: str):
            data = self.streams.get(name, b"")
            return audit_store._StreamSnapshot(
                name=name,
                data=data[-audit_store.STREAM_TAIL_BYTES :],
                head=json.loads(data.splitlines()[-1]) if data else None,
                length=len(data),
                etag=str(self.versions.get(name)) if name in self.streams else None,
            )

        def read_full(self, name: str) -> bytes:
            return self.streams.get(name, b"")

        def append(self, name: str, payload: bytes, snapshot) -> None:
            if "/events." in name:
                self.event_calls.append(snapshot.length)
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
                    self.versions[name] = 1
                    anchor = audit_store._build_anchor(
                        anchor_revision=1,
                        workspace_id="ws-audit",
                        workspace_revision=1,
                        workspace_event_hash=competing["event_hash"],
                        previous_hash=audit_store.GENESIS_HASH,
                        key_id=competing["key_id"],
                    )
                    anchor_name = audit_store._anchor_stream_name("ws-audit")
                    self.streams[anchor_name] = audit_store._line(anchor)
                    self.versions[anchor_name] = 1
                    raise audit_store._AppendConflict()
            current = self.streams.get(name, b"")
            current_etag = str(self.versions.get(name)) if name in self.streams else None
            if len(current) != snapshot.length or current_etag != snapshot.etag:
                raise audit_store._AppendConflict()
            self.streams[name] = current + payload
            self.versions[name] = self.versions.get(name, 0) + 1

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


class _SnapshotRaceBackend:
    def __init__(self, *, event_race: bool = False, anchor_race: bool = False, head_pair_race: bool = False) -> None:
        self.streams: dict[str, bytes] = {}
        self.versions: dict[str, int] = {}
        self.event_race = event_race
        self.anchor_race = anchor_race
        self.head_pair_race = head_pair_race
        self.anchor_was_read = False
        self.event_attempt_revisions: list[int] = []
        self.snapshot_reads = 0
        self.full_reads = 0
        self.legacy_reads = 0

    def _etag(self, name: str) -> str | None:
        return f'"v{self.versions.get(name, 0)}"' if name in self.streams else None

    def read_snapshot(self, name: str):
        self.snapshot_reads += 1
        if self.head_pair_race and "/anchors" in name:
            self.anchor_was_read = True
        elif self.head_pair_race and self.anchor_was_read and "/events" in name:
            self.head_pair_race = False
            event_head = json.loads(self.streams[name].splitlines()[-1])
            anchor_name = audit_store._anchor_stream_name("ws-audit")
            anchor_head = json.loads(self.streams[anchor_name].splitlines()[-1])
            second = audit_store._build_event(
                _actor(), "file.edit", _resource("file-race-2"), {},
                revision=2, previous_hash=event_head["event_hash"],
            )
            second_anchor = audit_store._build_anchor(
                anchor_revision=2,
                workspace_id="ws-audit",
                workspace_revision=2,
                workspace_event_hash=second["event_hash"],
                previous_hash=anchor_head["anchor_hash"],
                key_id=second["key_id"],
            )
            third = audit_store._build_event(
                _actor(), "file.edit", _resource("file-race-3"), {},
                revision=3, previous_hash=second["event_hash"],
            )
            self._commit(name, audit_store._line(second))
            self._commit(anchor_name, audit_store._line(second_anchor))
            self._commit(name, audit_store._line(third))
        data = self.streams.get(name, b"")
        tail = data[-audit_store.STREAM_TAIL_BYTES :]
        head = json.loads(data.splitlines()[-1]) if data else None
        return audit_store._StreamSnapshot(name=name, data=tail, head=head, length=len(data), etag=self._etag(name))

    def read_full(self, name: str) -> bytes:
        self.full_reads += 1
        return self.streams.get(name, b"")

    def read(self, name: str) -> bytes:
        self.legacy_reads += 1
        return self.streams.get(name, b"")

    def list_names(self, prefix: str) -> list[str]:
        self.legacy_reads += 1
        return sorted(name for name in self.streams if name.startswith(prefix))

    def _commit(self, name: str, payload: bytes) -> None:
        self.streams[name] = self.streams.get(name, b"") + payload
        self.versions[name] = self.versions.get(name, 0) + 1

    def append(self, name: str, payload: bytes, snapshot) -> None:
        if isinstance(snapshot, int):
            raise AssertionError("append position was not bound to the validated stream snapshot")
        value = json.loads(payload)
        if "/events" in name:
            self.event_attempt_revisions.append(int(value["revision"]))
            if self.event_race:
                self.event_race = False
                competitor = audit_store._build_event(
                    _actor(),
                    "message.create",
                    {"workspace_id": "ws-audit", "resource_type": "message", "resource_id": "message-other"},
                    {},
                    revision=1,
                    previous_hash=audit_store.GENESIS_HASH,
                )
                self._commit(name, audit_store._line(competitor))
                competitor_anchor = audit_store._build_anchor(
                    anchor_revision=1,
                    workspace_id="ws-audit",
                    workspace_revision=1,
                    workspace_event_hash=competitor["event_hash"],
                    previous_hash=audit_store.GENESIS_HASH,
                    key_id=competitor["key_id"],
                )
                self._commit(audit_store._anchor_stream_name("ws-audit"), audit_store._line(competitor_anchor))
        elif "/anchors" in name and self.anchor_race:
            self.anchor_race = False
            self._commit(name, payload)
        if len(self.streams.get(name, b"")) != snapshot.length or self._etag(name) != snapshot.etag:
            raise audit_store._AppendConflict("deterministic interleaving")
        self._commit(name, payload)


def test_event_append_reloads_revalidates_and_rebuilds_after_snapshot_interleaving(monkeypatch) -> None:
    remote = _SnapshotRaceBackend(event_race=True)
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    event = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    assert remote.event_attempt_revisions == [1, 2]
    assert event["revision"] == 2
    assert audit_store.list_audit_events("ws-audit")["revision"] == 2


def test_anchor_append_is_bound_to_validated_snapshot_during_interleaving(monkeypatch) -> None:
    remote = _SnapshotRaceBackend(anchor_race=True)
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    event = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    assert event["revision"] == 1
    assert audit_store.list_audit_events("ws-audit")["revision"] == 1


def test_inconsistent_cross_stream_head_pair_is_reconfirmed_and_retried(monkeypatch) -> None:
    remote = _SnapshotRaceBackend()
    first = audit_store._build_event(
        _actor(), "file.create", _resource("file-race-1"), {}, revision=1, previous_hash=audit_store.GENESIS_HASH
    )
    first_anchor = audit_store._build_anchor(
        anchor_revision=1,
        workspace_id="ws-audit",
        workspace_revision=1,
        workspace_event_hash=first["event_hash"],
        previous_hash=audit_store.GENESIS_HASH,
        key_id=first["key_id"],
    )
    remote._commit(audit_store._event_stream_name("ws-audit"), audit_store._line(first))
    remote._commit(audit_store._anchor_stream_name("ws-audit"), audit_store._line(first_anchor))
    remote.head_pair_race = True
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    event = audit_store.record_audit_event(_actor(), "file.delete", _resource("file-race-3"), {})

    assert event["revision"] == 4
    assert audit_store.list_audit_events("ws-audit")["revision"] == 4


def test_mutation_uses_bounded_tail_snapshots_not_full_history(monkeypatch) -> None:
    remote = _SnapshotRaceBackend()
    previous_event_hash = audit_store.GENESIS_HASH
    previous_anchor_hash = audit_store.GENESIS_HASH
    event_name = "workspaces/ws-audit/events.jsonl"
    anchor_name = "workspaces/ws-audit/anchors.jsonl"
    for revision in range(1, 301):
        event = audit_store._build_event(
            _actor(), "file.edit", _resource(f"file-{revision}"), {},
            revision=revision, previous_hash=previous_event_hash,
        )
        anchor = audit_store._build_anchor(
            anchor_revision=revision,
            workspace_id="ws-audit",
            workspace_revision=revision,
            workspace_event_hash=event["event_hash"],
            previous_hash=previous_anchor_hash,
            key_id=event["key_id"],
        )
        remote._commit(event_name, audit_store._line(event))
        remote._commit(anchor_name, audit_store._line(anchor))
        previous_event_hash = event["event_hash"]
        previous_anchor_hash = anchor["anchor_hash"]
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    appended = audit_store.record_audit_event(_actor(), "file.delete", _resource("file-300"), {})

    assert appended["revision"] == 301
    assert remote.full_reads == 0
    assert remote.legacy_reads == 0
    assert remote.snapshot_reads <= 8


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
