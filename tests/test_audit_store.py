import base64
import hashlib
import json
import re
import secrets
from types import SimpleNamespace

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
    monkeypatch.setenv("DF_AUDIT_HMAC_SCOPE_KEY_ID", "test-v1")
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
    monkeypatch.delenv("DF_AUDIT_HMAC_SCOPE_KEY_ID", raising=False)

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


def test_preview_environment_overrides_container_app_production_detection(monkeypatch) -> None:
    monkeypatch.setenv("CONTAINER_APP_NAME", "ca-dataforge-backend")
    monkeypatch.setenv("DF_ENVIRONMENT", "preview")

    assert audit_store._is_production() is False

    monkeypatch.setenv("DF_ENVIRONMENT", "production")

    assert audit_store._is_production() is True


def test_production_audit_store_uses_system_managed_identity(monkeypatch) -> None:
    captured: dict[str, object] = {}
    managed_identity = object()
    default_credential = object()

    class _Service:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def get_container_client(self, name: str) -> str:
            return name

    monkeypatch.setenv("DF_ENVIRONMENT", "production")
    monkeypatch.setattr(audit_store, "ManagedIdentityCredential", lambda: managed_identity, raising=False)
    monkeypatch.setattr(audit_store, "DefaultAzureCredential", lambda: default_credential)
    monkeypatch.setattr(audit_store, "BlobServiceClient", _Service)

    audit_store._BlobAppendBackend(account_name="dataforgeprod", managed_identity_only=True)

    assert captured["credential"] is managed_identity


def test_production_contract_check_uses_system_managed_identity_even_with_azure_client_id(monkeypatch) -> None:
    calls: list[str] = []

    class _ManagedIdentity:
        def get_token(self, scope: str):
            calls.append(scope)
            return SimpleNamespace(token="managed-identity-token")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"properties": {}}'

    def unexpected_default_credential():
        raise AssertionError("production audit contract validation must not select DefaultAzureCredential")

    monkeypatch.setenv("DF_ENVIRONMENT", "production")
    monkeypatch.setenv("AZURE_CLIENT_ID", "unattached-user-assigned-identity")
    monkeypatch.setattr(audit_store, "ManagedIdentityCredential", _ManagedIdentity)
    monkeypatch.setattr(audit_store, "DefaultAzureCredential", unexpected_default_credential)
    monkeypatch.setattr(audit_store.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    assert audit_store._management_get_json("/subscriptions/sub/resource") == {"properties": {}}
    assert calls == ["https://management.azure.com/.default"]


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
    monkeypatch.setenv("DF_AUDIT_HMAC_SCOPE_KEY_ID", "old")
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
    first = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    interrupted = audit_store._build_event(
        _actor(),
        "file.edit",
        _resource(),
        {},
        revision=2,
        previous_hash=first["event_hash"],
    )
    event_path = audit_store._local_stream_path(audit_store._event_stream_name("ws-audit", 1))
    with event_path.open("ab") as stream:
        stream.write(audit_store._line(interrupted))

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
    other_scope = audit_store._workspace_scope_id("ws-other")

    def fail_next_workspace_anchor(self, name, payload, expected):
        if f"workspaces/{other_scope}/anchors/" in name and not interrupted["value"]:
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
    name = audit_store._event_stream_name("ws-audit")
    first = audit_store._build_event(
        _actor(), "file.create", _resource("file-1"), {}, revision=1, previous_hash=audit_store.GENESIS_HASH
    )
    forged_second_genesis = audit_store._build_event(
        _actor(), "file.create", _resource("file-2"), {}, revision=1, previous_hash=audit_store.GENESIS_HASH
    )
    path = audit_store._local_stream_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audit_store._line(first) + audit_store._line(forged_second_genesis))

    with pytest.raises(audit_store.AuditIntegrityError, match="physical record|anchor"):
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
    container = {
        "hasLegalHold": True,
        "legalHold": {
            "hasLegalHold": True,
            "tags": [{"tag": "dataforgeaudit"}],
            "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": True},
        },
    }
    sealed_policy = {"state": "Locked", "allowProtectedAppendWrites": False}
    sealed_container = {
        "hasLegalHold": True,
        "legalHold": {
            "hasLegalHold": True,
            "tags": [{"tag": "dataforgeaudit"}],
            "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": False},
        },
    }
    audit_store._validate_production_contract(
        service, policy, container, "dataforgeaudit", sealed_policy, sealed_container
    )

    for broken_service, broken_policy in [
        ({**service, "isVersioningEnabled": False}, policy),
        ({**service, "deleteRetentionPolicy": {"enabled": False}}, policy),
        ({**service, "containerDeleteRetentionPolicy": {"enabled": False}}, policy),
        (service, {**policy, "state": "Unlocked"}),
        (service, {**policy, "allowProtectedAppendWrites": False}),
    ]:
        with pytest.raises(audit_store.AuditPersistenceError, match="contract"):
            audit_store._validate_production_contract(
                broken_service,
                broken_policy,
                container,
                "dataforgeaudit",
                sealed_policy,
                sealed_container,
            )


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
    monkeypatch.setenv("DF_AUDIT_LEGAL_HOLD_TAG", "dataforgeaudit")
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
        sealed = "/containers/dataforge-audit-sealed" in path
        if "immutabilityPolicies" in path:
            return {
                "properties": {
                    "state": "Unlocked" if broken["value"] else "Locked",
                    "allowProtectedAppendWrites": not sealed,
                }
            }
        if "/containers/" in path:
            return {
                "properties": {
                    "hasLegalHold": True,
                    "legalHold": {
                        "hasLegalHold": True,
                        "tags": [{"tag": "dataforgeaudit"}],
                        "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": not sealed},
                    },
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
    assert len(calls) == 5

    broken["value"] = True
    clock["now"] += audit_store.PRODUCTION_CONTRACT_CACHE_TTL_SECONDS + 0.01
    with pytest.raises(audit_store.AuditPersistenceError, match="contract"):
        audit_store._verify_production_storage_contract("writeaccount")
    assert len(calls) == 10


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
    remote = _SnapshotRaceBackend(event_race=True)
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)

    event = audit_store.record_audit_event(
        _actor(),
        "file.create",
        _resource(),
        {"correlation": {"request_id": "req-local"}},
    )

    assert remote.event_attempt_positions[0] == 0
    assert remote.event_attempt_positions[1] > 0
    assert event["revision"] == 2
    persisted = audit_store.list_audit_events("ws-audit")["events"]
    assert event["previous_hash"] == persisted[1]["event_hash"]
    assert len(persisted) == 2


class _SnapshotRaceBackend:
    def __init__(self, *, event_race: bool = False, anchor_race: bool = False, head_pair_race: bool = False) -> None:
        self.streams: dict[str, bytes] = {}
        self.sealed_streams: dict[str, bytes] = {}
        self.versions: dict[str, int] = {}
        self.event_race = event_race
        self.anchor_race = anchor_race
        self.head_pair_race = head_pair_race
        self.anchor_was_read = False
        self.event_attempt_revisions: list[int] = []
        self.event_attempt_positions: list[int] = []
        self.snapshot_reads = 0
        self.full_reads = 0
        self.range_reads = 0
        self.list_reads = 0

    def read_snapshot(self, name: str):
        self.snapshot_reads += 1
        if self.head_pair_race and "/anchors" in name:
            self.anchor_was_read = True
        elif self.head_pair_race and self.anchor_was_read and "/events" in name:
            self.head_pair_race = False
            audit_store.record_audit_event(_actor(), "file.edit", _resource("file-race-2"), {})
            audit_store.record_audit_event(_actor(), "file.edit", _resource("file-race-3"), {})
        sealed = name in self.sealed_streams
        data = self.sealed_streams.get(name, self.streams.get(name, b""))
        tail = data[-audit_store.STREAM_TAIL_BYTES :]
        return audit_store._snapshot_from_tail(
            name,
            tail,
            len(data),
            self._etag(name, sealed=sealed),
            data.count(b"\n"),
            sealed=sealed,
            content_sha256=hashlib.sha256(data).hexdigest() if sealed else None,
        )

    def _etag(self, name: str, *, sealed: bool = False) -> str | None:
        if sealed and name in self.sealed_streams:
            return f'"sealed-{len(self.sealed_streams[name]):x}"'
        return f'"v{self.versions.get(name, 0)}"' if name in self.streams else None

    def read_full(self, name: str, snapshot) -> bytes:
        self.full_reads += 1
        data = self.sealed_streams.get(name, self.streams.get(name, b""))
        if len(data) != snapshot.length or self._etag(name, sealed=snapshot.sealed) != snapshot.etag:
            raise audit_store._AppendConflict("deterministic stream changed during full read")
        return data

    def read_range(self, name: str, offset: int, length: int, snapshot) -> bytes:
        self.range_reads += 1
        data = self.sealed_streams.get(name, self.streams.get(name, b""))
        if len(data) != snapshot.length or self._etag(name, sealed=snapshot.sealed) != snapshot.etag:
            raise audit_store._AppendConflict("deterministic range interleaving")
        return data[offset : offset + length]

    def list_names(self, prefix: str, limit: int | None = None) -> list[str]:
        self.list_reads += 1
        names = sorted({name for name in (*self.streams, *self.sealed_streams) if name.startswith(prefix)})
        return names[:limit] if limit is not None else names

    def _commit(self, name: str, payload: bytes) -> None:
        self.streams[name] = self.streams.get(name, b"") + payload
        self.versions[name] = self.versions.get(name, 0) + 1

    def append(self, name: str, payload: bytes, snapshot) -> None:
        if isinstance(snapshot, int):
            raise AssertionError("append position was not bound to the validated stream snapshot")
        if snapshot.sealed or name in self.sealed_streams:
            raise audit_store.AuditIntegrityError("sealed audit segment cannot be appended")
        value = json.loads(payload)
        if "/events/" in name:
            self.event_attempt_revisions.append(int(value["revision"]))
            self.event_attempt_positions.append(snapshot.length)
            if self.event_race:
                self.event_race = False
                competitor = audit_store._build_event(
                    _actor(),
                    "message.create",
                    {"workspace_id": "ws-audit", "resource_type": "message", "resource_id": "message-other"},
                    {},
                    revision=int(value["revision"]),
                    previous_hash=str(value["previous_hash"]),
                )
                self._commit(name, audit_store._line(competitor))
        elif "/anchors/" in name and "global/anchors/" not in name and self.anchor_race:
            self.anchor_race = False
            self._commit(name, payload)
        if len(self.streams.get(name, b"")) != snapshot.length or self._etag(name) != snapshot.etag:
            raise audit_store._AppendConflict("deterministic interleaving")
        self._commit(name, payload)

    def seal(self, name: str, snapshot) -> object:
        if name not in self.sealed_streams:
            if len(self.streams.get(name, b"")) != snapshot.length or self._etag(name) != snapshot.etag:
                raise audit_store._AppendConflict("deterministic seal interleaving")
            self.sealed_streams[name] = self.streams[name]
        return self.read_snapshot(name)


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
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)
    audit_store.record_audit_event(_actor(), "file.create", _resource("file-race-1"), {})
    remote.head_pair_race = True

    event = audit_store.record_audit_event(_actor(), "file.delete", _resource("file-race-3"), {})

    assert event["revision"] == 4
    assert audit_store.list_audit_events("ws-audit")["revision"] == 4


def test_mutation_uses_bounded_tail_snapshots_not_full_history(monkeypatch) -> None:
    remote = _SnapshotRaceBackend()
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)
    for revision in range(1, 301):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(f"file-{revision}"), {})
    remote.snapshot_reads = 0
    remote.full_reads = 0
    remote.range_reads = 0
    remote.list_reads = 0

    appended = audit_store.record_audit_event(_actor(), "file.delete", _resource("file-300"), {})

    assert appended["revision"] == 301
    assert remote.full_reads == 0
    assert remote.list_reads <= 6
    assert remote.snapshot_reads <= 8
    assert remote.range_reads <= 3


def _canonical_local_files(predicate) -> list:
    canonical: dict[str, object] = {}
    for path in audit_store.AUDIT_DIR.rglob("*.jsonl"):
        if not predicate(path):
            continue
        relative = path.relative_to(audit_store.AUDIT_DIR)
        parts = relative.parts[1:] if relative.parts and relative.parts[0] in {"active", "sealed"} else relative.parts
        key = "/".join(parts)
        if key not in canonical or "sealed" in relative.parts:
            canonical[key] = path
    return sorted(canonical.values(), key=lambda path: int(path.stem))


def _canonical_stream_name(path) -> str:
    relative = path.relative_to(audit_store.AUDIT_DIR)
    parts = relative.parts[1:] if relative.parts and relative.parts[0] in {"active", "sealed"} else relative.parts
    return "/".join(parts)


def _workspace_event_files() -> list:
    return _canonical_local_files(
        lambda path: "workspaces" in path.parts and ("events" in path.parts or path.name == "events.jsonl")
    )


def _workspace_anchor_files() -> list:
    return _canonical_local_files(
        lambda path: "workspaces" in path.parts and ("anchors" in path.parts or path.name == "anchors.jsonl")
    )


def _workspace_receipt_files() -> list:
    return _canonical_local_files(
        lambda path: "global" in path.parts and "workspaces" in path.parts and "receipts" in path.parts
    )


def _global_anchor_files() -> list:
    return _canonical_local_files(
        lambda path: "global" in path.parts and "anchors" in path.parts
    )


def test_r3_workspace_anchor_commits_exact_event_stream_coordinates() -> None:
    event = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    event_file = _workspace_event_files()[-1]
    anchor = json.loads(_workspace_anchor_files()[-1].read_bytes().splitlines()[-1])
    relative_event_name = _canonical_stream_name(event_file)

    assert anchor["workspace_revision"] == event["revision"] == 1
    assert anchor["workspace_event_hash"] == event["event_hash"]
    assert anchor["event_segment_index"] == 1
    assert anchor["event_stream_name"] == relative_event_name
    assert anchor["event_stream_length"] == event_file.stat().st_size
    assert anchor["event_segment_record_count"] == 1


def test_r3_duplicate_latest_event_record_fails_before_any_new_mutation() -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    event_file = _workspace_event_files()[-1]
    duplicate = event_file.read_bytes().splitlines(keepends=True)[-1]
    event_file.write_bytes(event_file.read_bytes() + duplicate)
    corrupted = event_file.read_bytes()

    with pytest.raises(audit_store.AuditIntegrityError, match="physical|duplicate|record"):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    assert event_file.read_bytes() == corrupted


def test_r3_duplicate_latest_workspace_anchor_fails_before_event_append() -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    event_file = _workspace_event_files()[-1]
    anchor_file = _workspace_anchor_files()[-1]
    anchor_file.write_bytes(anchor_file.read_bytes() + anchor_file.read_bytes().splitlines(keepends=True)[-1])
    event_bytes = event_file.read_bytes()

    with pytest.raises(audit_store.AuditIntegrityError, match="physical|duplicate|record"):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    assert event_file.read_bytes() == event_bytes


def test_r3_one_gap_recovery_rejects_an_extra_replayed_candidate_record() -> None:
    first = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    candidate = audit_store._build_event(
        _actor(),
        "file.edit",
        _resource("file-2"),
        {},
        revision=2,
        previous_hash=first["event_hash"],
    )
    event_file = _workspace_event_files()[-1]
    candidate_line = audit_store._line(candidate)
    event_file.write_bytes(event_file.read_bytes() + candidate_line + candidate_line)

    with pytest.raises(audit_store.AuditIntegrityError, match="exactly one|duplicate|record"):
        audit_store.record_audit_event(_actor(), "file.delete", _resource("file-2"), {})


def test_r3_global_anchor_commits_workspace_anchor_physical_coordinates() -> None:
    event = audit_store.record_audit_event(_actor(), "file.create", _resource(), {})

    workspace_anchor_file = _workspace_anchor_files()[-1]
    workspace_anchor = json.loads(workspace_anchor_file.read_bytes().splitlines()[-1])
    global_anchor_file = _global_anchor_files()[-1]
    global_anchor = json.loads(global_anchor_file.read_bytes().splitlines()[-1])

    assert global_anchor["global_sequence"] == 1
    assert global_anchor["workspace_revision"] == event["revision"]
    assert global_anchor["workspace_anchor_hash"] == workspace_anchor["anchor_hash"]
    assert global_anchor["workspace_anchor_segment_index"] == 1
    assert global_anchor["workspace_anchor_stream_name"] == _canonical_stream_name(workspace_anchor_file)
    assert global_anchor["workspace_anchor_stream_length"] == workspace_anchor_file.stat().st_size
    assert global_anchor["workspace_anchor_segment_record_count"] == 1


def test_r3_duplicate_latest_global_anchor_is_detected_from_signed_physical_receipt() -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource(), {})
    event_file = _workspace_event_files()[-1]
    global_file = _global_anchor_files()[-1]
    global_file.write_bytes(global_file.read_bytes() + global_file.read_bytes().splitlines(keepends=True)[-1])
    event_bytes = event_file.read_bytes()

    with pytest.raises(audit_store.AuditIntegrityError, match="global.*physical|duplicate|record"):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(), {})

    assert event_file.read_bytes() == event_bytes


def test_r3_global_history_rejects_workspace_event_and_anchor_prefix_rollback() -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource("file-1"), {})
    audit_store.record_audit_event(_actor(), "file.edit", _resource("file-2"), {})
    event_file = _workspace_event_files()[0]
    anchor_file = _workspace_anchor_files()[0]
    event_file.write_bytes(event_file.read_bytes().splitlines(keepends=True)[0])
    anchor_file.write_bytes(anchor_file.read_bytes().splitlines(keepends=True)[0])

    with pytest.raises(audit_store.AuditIntegrityError, match="global|rollback|prefix"):
        audit_store.list_audit_events("ws-audit")


def test_r3_global_head_rejects_rolled_back_workspace_prefix_before_mutation() -> None:
    audit_store.record_audit_event(_actor(), "file.create", _resource("file-1"), {})
    audit_store.record_audit_event(_actor(), "file.edit", _resource("file-2"), {})
    event_file = _workspace_event_files()[0]
    anchor_file = _workspace_anchor_files()[0]
    event_file.write_bytes(event_file.read_bytes().splitlines(keepends=True)[0])
    anchor_file.write_bytes(anchor_file.read_bytes().splitlines(keepends=True)[0])
    event_bytes = event_file.read_bytes()

    with pytest.raises(audit_store.AuditIntegrityError, match="global.*later|prefix"):
        audit_store.record_audit_event(_actor(), "file.delete", _resource("file-3"), {})

    assert event_file.read_bytes() == event_bytes


def test_r3_blob_latest_segment_lookup_consumes_one_bounded_page() -> None:
    calls: list[int] = []

    class Item:
        def __init__(self, name: str) -> None:
            self.name = name

    class Listing:
        def __iter__(self):
            raise AssertionError("bounded mutation lookup must not enumerate full segment history")

        def by_page(self):
            yield [Item("events/00000000/99999999.jsonl")]
            raise AssertionError("bounded mutation lookup consumed more than one page")

    class Container:
        def list_blobs(self, *, name_starts_with: str, results_per_page: int):
            assert name_starts_with == "events/"
            calls.append(results_per_page)
            return Listing()

    backend = audit_store._BlobAppendBackend.__new__(audit_store._BlobAppendBackend)
    backend.container = Container()
    backend.sealed_container = Container()

    assert backend.list_names("events/", limit=1) == ["events/00000000/99999999.jsonl"]
    assert calls == [1, 1]


def test_r4_blob_seal_is_etag_bound_and_sealed_copy_is_canonical(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 2)
    name = "events/99999998/00000001.jsonl"
    source_data = b'{"revision":1}\n{"revision":2}\n'
    calls: list[dict] = []

    class Downloader:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def readall(self) -> bytes:
            return self.data

    class Blob:
        def __init__(self, *, data: bytes = b"", blob_type: str, etag: str, metadata=None) -> None:
            self.data = data
            self.blob_type = blob_type
            self.etag = etag
            self.metadata = metadata or {}
            self.exists = bool(data)
            self.url = f"https://writeaccount.blob.core.windows.net/container/{name}"

        def get_blob_properties(self):
            if not self.exists:
                raise audit_store.ResourceNotFoundError("missing")
            return type(
                "Properties",
                (),
                {
                    "size": len(self.data),
                    "etag": self.etag,
                    "blob_type": self.blob_type,
                    "metadata": self.metadata,
                    "append_blob_committed_block_count": self.data.count(b"\n"),
                },
            )()

        def download_blob(self, *, offset=0, length=None, **kwargs):
            end = None if length is None else offset + length
            return Downloader(self.data[offset:end])

        def upload_blob_from_url(self, source_url: str, **kwargs) -> None:
            calls.append({"source_url": source_url, **kwargs})
            assert kwargs["source_etag"] == active.etag
            self.data = active.data
            self.blob_type = "BlockBlob"
            self.etag = '"sealed-v1"'
            self.metadata = kwargs["metadata"]
            self.exists = True

    class Container:
        def __init__(self, blob: Blob) -> None:
            self.blob = blob

        def get_blob_client(self, requested_name: str) -> Blob:
            assert requested_name == name
            return self.blob

    class Credential:
        def get_token(self, scope: str):
            assert scope == "https://storage.azure.com/.default"
            return type("Token", (), {"token": "managed-identity-token"})()

    active = Blob(data=source_data, blob_type="AppendBlob", etag='"active-v1"')
    sealed_blob = Blob(blob_type="BlockBlob", etag='"missing"')
    backend = audit_store._BlobAppendBackend.__new__(audit_store._BlobAppendBackend)
    backend.container = Container(active)
    backend.sealed_container = Container(sealed_blob)
    backend.credential = Credential()
    source = audit_store._snapshot_from_tail(
        name, source_data, len(source_data), active.etag, 2
    )

    sealed = backend.seal(name, source)
    active.data += source_data.splitlines(keepends=True)[-1]
    canonical = backend.read_snapshot(name)

    assert len(calls) == 1
    assert calls[0]["source_authorization"] == "Bearer managed-identity-token"
    assert calls[0]["source_match_condition"] == audit_store.MatchConditions.IfNotModified
    assert sealed.sealed is True
    assert canonical.sealed is True
    assert canonical.length == len(source_data)
    assert canonical.head == {"revision": 2}


@pytest.mark.parametrize("precreated", [False, True])
def test_seal_rejects_creator_or_cross_replica_destination_with_different_earlier_valid_records(
    monkeypatch, precreated: bool
) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 200)
    name = "events/99999998/00000001.jsonl"
    events: list[dict] = []
    previous_hash = audit_store.GENESIS_HASH
    for revision in range(1, 201):
        event = audit_store._build_event(
            _actor(),
            "file.edit",
            _resource(f"file-{revision}"),
            {},
            revision=revision,
            previous_hash=previous_hash,
            event_id=f"event_{revision:032x}",
            at="2026-07-14T00:00:00Z",
        )
        assert event["event_hash"] == audit_store._hash_event(event)
        events.append(event)
        previous_hash = event["event_hash"]
    source_data = b"".join(audit_store._line(event) for event in events)
    malicious_data = b"".join(
        audit_store._line(event) for event in [events[1], events[0], *events[2:]]
    )
    assert len(source_data) > audit_store.STREAM_TAIL_BYTES
    assert len(malicious_data) == len(source_data)
    assert malicious_data[-audit_store.STREAM_TAIL_BYTES :] == source_data[-audit_store.STREAM_TAIL_BYTES :]
    assert malicious_data != source_data

    source_etag = '"active-v1"'
    source = audit_store._snapshot_from_tail(
        name,
        source_data[-audit_store.STREAM_TAIL_BYTES :],
        len(source_data),
        source_etag,
        200,
    )
    source_digest = hashlib.sha256(source_data).hexdigest()
    forged_metadata = audit_store._build_seal_metadata(name, source, source_digest)
    assert audit_store._validated_seal_metadata(
        name, len(source_data), 200, forged_metadata
    ) == source_digest

    class Downloader:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def readall(self) -> bytes:
            return self.data

    class Blob:
        def __init__(self, *, data: bytes, blob_type: str, etag: str, metadata=None, precreated=False) -> None:
            self.data = data
            self.blob_type = blob_type
            self.etag = etag
            self.metadata = metadata or {}
            self.precreated = precreated
            self.download_calls: list[tuple[int, int | None]] = []
            self.url = f"https://writeaccount.blob.core.windows.net/container/{name}"

        def get_blob_properties(self):
            return type(
                "Properties",
                (),
                {
                    "size": len(self.data),
                    "etag": self.etag,
                    "blob_type": self.blob_type,
                    "metadata": self.metadata,
                    "append_blob_committed_block_count": self.data.count(b"\n"),
                },
            )()

        def download_blob(self, *, offset=0, length=None, **kwargs):
            self.download_calls.append((offset, length))
            end = None if length is None else offset + length
            return Downloader(self.data[offset:end])

        def upload_blob_from_url(self, source_url: str, **kwargs) -> None:
            if self.precreated:
                raise audit_store.ResourceExistsError("another replica sealed first")
            self.data = malicious_data
            self.blob_type = "BlockBlob"
            self.etag = '"sealed-this-replica"'
            self.metadata = kwargs["metadata"]

    class Container:
        def __init__(self, blob: Blob) -> None:
            self.blob = blob

        def get_blob_client(self, requested_name: str) -> Blob:
            assert requested_name == name
            return self.blob

    class Credential:
        def get_token(self, scope: str):
            return type("Token", (), {"token": "managed-identity-token"})()

    active = Blob(data=source_data, blob_type="AppendBlob", etag=source_etag)
    malicious = Blob(
        data=malicious_data if precreated else b"",
        blob_type="BlockBlob",
        etag='"sealed-other-replica"' if precreated else '"missing"',
        metadata=forged_metadata if precreated else {},
        precreated=precreated,
    )
    backend = audit_store._BlobAppendBackend.__new__(audit_store._BlobAppendBackend)
    backend.container = Container(active)
    backend.sealed_container = Container(malicious)
    backend.credential = Credential()

    with pytest.raises(audit_store.AuditIntegrityError, match="content digest does not match source"):
        backend.seal(name, source)
    assert active.download_calls == [(0, len(source_data))]
    assert malicious.download_calls[-1] == (0, len(malicious_data))


def test_governance_full_read_rejects_reordered_earlier_json_in_sealed_segment(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 120)
    for revision in range(1, 122):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(f"file-{revision}"), {})

    assert audit_store.list_audit_events("ws-audit")["revision"] == 121
    first_segment = _workspace_event_files()[0]
    assert "sealed" in first_segment.relative_to(audit_store.AUDIT_DIR).parts
    original = first_segment.read_bytes()
    lines = original.splitlines(keepends=True)
    first_event = json.loads(lines[0])
    reordered_event = {key: first_event[key] for key in reversed(tuple(first_event))}
    reordered_line = json.dumps(
        reordered_event,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    tampered = b"".join([reordered_line, *lines[1:]])

    assert reordered_line != lines[0]
    assert len(reordered_line) == len(lines[0])
    assert json.loads(reordered_line) == first_event
    assert first_event["event_hash"] == audit_store._hash_event(first_event)
    assert len(tampered) == len(original)
    assert tampered.count(b"\n") == original.count(b"\n")
    assert len(original) > audit_store.STREAM_TAIL_BYTES
    assert tampered[-audit_store.STREAM_TAIL_BYTES :] == original[-audit_store.STREAM_TAIL_BYTES :]

    first_segment.chmod(0o600)
    first_segment.write_bytes(tampered)

    with pytest.raises(audit_store.AuditIntegrityError, match="content digest"):
        audit_store.list_audit_events("ws-audit")


def test_r3_segment_rotation_is_deterministic_and_old_segments_are_not_reopened(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 2)
    audit_store.record_audit_event(_actor(), "file.create", _resource("file-1"), {})
    audit_store.record_audit_event(_actor(), "file.edit", _resource("file-2"), {})
    first_event_segment = _workspace_event_files()[0]
    first_anchor_segment = _workspace_anchor_files()[0]
    first_event_bytes = first_event_segment.read_bytes()
    first_anchor_bytes = first_anchor_segment.read_bytes()

    third = audit_store.record_audit_event(_actor(), "file.delete", _resource("file-3"), {})

    assert third["revision"] == 3
    assert [path.name for path in _workspace_event_files()] == ["00000001.jsonl", "00000002.jsonl"]
    assert [path.name for path in _workspace_anchor_files()] == ["00000001.jsonl", "00000002.jsonl"]
    assert first_event_segment.read_bytes() == first_event_bytes
    assert first_anchor_segment.read_bytes() == first_anchor_bytes
    assert len(_global_anchor_files()) == 2


def test_r3_concurrent_first_append_to_rotated_segment_revalidates_and_rebuilds(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 2)
    remote = _SnapshotRaceBackend()
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)
    audit_store.record_audit_event(_actor(), "file.create", _resource("file-1"), {})
    audit_store.record_audit_event(_actor(), "file.edit", _resource("file-2"), {})
    first_segments = {
        name: payload
        for name, payload in remote.streams.items()
        if name.endswith("00000001.jsonl")
    }
    remote.event_race = True

    appended = audit_store.record_audit_event(_actor(), "file.delete", _resource("file-4"), {})

    assert appended["revision"] == 4
    assert remote.event_attempt_revisions[-2:] == [3, 4]
    assert all(remote.streams[name] == payload for name, payload in first_segments.items())
    assert audit_store.list_audit_events("ws-audit")["revision"] == 4


def test_r3_mutation_call_count_is_constant_across_many_segments(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 2)
    remote = _SnapshotRaceBackend()
    monkeypatch.setattr(audit_store, "_backend", lambda: remote)
    for revision in range(1, 21):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(f"file-{revision}"), {})
    remote.snapshot_reads = 0
    remote.full_reads = 0
    remote.range_reads = 0
    remote.list_reads = 0

    appended = audit_store.record_audit_event(_actor(), "file.delete", _resource("file-21"), {})

    assert appended["revision"] == 21
    assert remote.full_reads == 0
    assert remote.list_reads <= 6
    assert remote.snapshot_reads <= 16
    assert remote.range_reads <= 3


def test_full_segment_hashing_runs_only_when_a_segment_rotates(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 3)
    original = audit_store._read_local_segment_bytes
    hashed: list[str] = []

    def tracked(path, snapshot, label):
        hashed.append(label)
        return original(path, snapshot, label)

    monkeypatch.setattr(audit_store, "_read_local_segment_bytes", tracked)
    for revision in range(1, 4):
        audit_store.record_audit_event(_actor(), "file.edit", _resource(f"file-{revision}"), {})
    assert hashed == []

    audit_store.record_audit_event(_actor(), "file.edit", _resource("file-4"), {})
    rotation_hashes = len(hashed)
    assert rotation_hashes > 0

    audit_store.record_audit_event(_actor(), "file.edit", _resource("file-5"), {})
    assert len(hashed) == rotation_hashes


def test_r3_raw_workspace_and_resource_ids_are_hmac_pseudonyms_everywhere() -> None:
    raw_workspace = "eyJhbGciOiJIUzI1NiJ9.workspace-secret.signature"
    raw_resource = "AccountKey=resource-secret;ApiKey=sk-live-secret"
    event = audit_store.record_audit_event(
        _actor(),
        "file.create",
        {"workspace_id": raw_workspace, "resource_type": "file", "resource_id": raw_resource},
        {},
    )
    response = audit_store.list_audit_events(raw_workspace)
    persisted = b"".join(path.read_bytes() for path in audit_store.AUDIT_DIR.rglob("*.*") if path.is_file())
    paths = "\n".join(path.relative_to(audit_store.AUDIT_DIR).as_posix() for path in audit_store.AUDIT_DIR.rglob("*"))

    assert re.fullmatch(r"ws_[0-9a-f]{40}", event["workspace_id"])
    assert re.fullmatch(r"res_[0-9a-f]{40}", event["resource_id"])
    assert response["workspace_id"] == event["workspace_id"]
    assert raw_workspace.encode() not in persisted
    assert raw_resource.encode() not in persisted
    assert raw_workspace not in paths
    assert raw_resource not in paths
    assert raw_workspace not in json.dumps(response)
    assert raw_resource not in json.dumps(response)


def test_r3_production_contract_requires_configured_legal_hold_tag() -> None:
    service = {
        "isVersioningEnabled": True,
        "deleteRetentionPolicy": {"enabled": True, "days": 30},
        "containerDeleteRetentionPolicy": {"enabled": True, "days": 30},
    }
    policy = {"state": "Locked", "allowProtectedAppendWrites": True}
    container = {
        "hasLegalHold": True,
        "legalHold": {
            "hasLegalHold": True,
            "tags": [{"tag": "dataforgeaudit"}],
            "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": True},
        },
    }
    sealed_policy = {"state": "Locked", "allowProtectedAppendWrites": False}
    sealed_container = {
        "hasLegalHold": True,
        "legalHold": {
            "hasLegalHold": True,
            "tags": [{"tag": "dataforgeaudit"}],
            "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": False},
        },
    }

    audit_store._validate_production_contract(
        service, policy, container, "dataforgeaudit", sealed_policy, sealed_container
    )
    for broken in [
        {**container, "hasLegalHold": False},
        {**container, "legalHold": {**container["legalHold"], "tags": [{"tag": "otherhold"}]}},
        {
            **container,
            "legalHold": {
                **container["legalHold"],
                "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": False},
            },
        },
    ]:
        with pytest.raises(audit_store.AuditPersistenceError, match="legal hold|contract"):
            audit_store._validate_production_contract(
                service, policy, broken, "dataforgeaudit", sealed_policy, sealed_container
            )


def test_r3_terraform_applies_exact_container_legal_hold_action() -> None:
    providers = (audit_store.ROOT / "infra" / "envs" / "dev" / "providers.tf").read_text(encoding="utf-8")
    storage = (audit_store.ROOT / "infra" / "modules" / "storage" / "main.tf").read_text(encoding="utf-8")

    assert 'source  = "Azure/azapi"' in providers
    assert 'resource "azapi_resource_action" "audit_legal_hold"' in storage
    assert 'type        = "Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01"' in storage
    assert 'action      = "setLegalHold"' in storage
    assert 'method      = "POST"' in storage
    assert "allowProtectedAppendWritesAll = true" in storage
    assert "tags                          = [var.audit_legal_hold_tag]" in storage


@pytest.mark.parametrize("rollback", ["empty", "older"])
def test_r4_global_workspace_receipt_rejects_a_rollback_after_b_advances_global_head(rollback: str) -> None:
    workspace_a = "workspace-a"
    workspace_b = "workspace-b"
    resource_a = {"workspace_id": workspace_a, "resource_type": "file", "resource_id": "file-a"}
    resource_b = {"workspace_id": workspace_b, "resource_type": "file", "resource_id": "file-b"}
    audit_store.record_audit_event(_actor(), "file.create", resource_a, {})
    if rollback == "older":
        audit_store.record_audit_event(_actor(), "file.edit", resource_a, {})
    audit_store.record_audit_event(_actor(), "file.create", resource_b, {})
    scope_a = audit_store._pseudonymize_workspace_id(workspace_a)
    local_files = sorted(
        path for path in audit_store.AUDIT_DIR.rglob("*.jsonl")
        if f"/workspaces/{scope_a}/" in f"/{path.relative_to(audit_store.AUDIT_DIR).as_posix()}"
        and "/global/" not in f"/{path.relative_to(audit_store.AUDIT_DIR).as_posix()}"
    )
    global_receipts = sorted(
        path for path in audit_store.AUDIT_DIR.rglob("*.jsonl")
        if f"global/workspaces/{scope_a}/receipts/" in path.relative_to(audit_store.AUDIT_DIR).as_posix()
    )
    assert global_receipts
    receipt_bytes = b"".join(path.read_bytes() for path in global_receipts)
    if rollback == "empty":
        for path in local_files:
            path.unlink()
    else:
        for path in local_files:
            path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])

    with pytest.raises(audit_store.AuditIntegrityError, match="global.*workspace|receipt|prefix"):
        audit_store.record_audit_event(_actor(), "file.delete", resource_a, {})

    assert b"".join(path.read_bytes() for path in global_receipts) == receipt_bytes
    assert audit_store.list_audit_events(workspace_b)["revision"] == 1


def test_r4_rotated_segments_use_sealed_canonical_copy_and_ignore_active_replay(monkeypatch) -> None:
    monkeypatch.setattr(audit_store, "MAX_RECORDS_PER_SEGMENT", 2)
    audit_store.record_audit_event(_actor(), "file.create", _resource("file-1"), {})
    audit_store.record_audit_event(_actor(), "file.edit", _resource("file-2"), {})
    audit_store.record_audit_event(_actor(), "file.delete", _resource("file-3"), {})
    active_segments = sorted((audit_store.AUDIT_DIR / "active").rglob("00000001.jsonl"))
    sealed_segments = sorted((audit_store.AUDIT_DIR / "sealed").rglob("00000001.jsonl"))
    assert active_segments
    assert len(active_segments) == len(sealed_segments)
    sealed_before = {path.relative_to(audit_store.AUDIT_DIR): path.read_bytes() for path in sealed_segments}
    for path in active_segments:
        duplicate = path.read_bytes().splitlines(keepends=True)[-1]
        with path.open("ab") as stream:
            stream.write(duplicate)

    listed = audit_store.list_audit_events("ws-audit")
    fourth = audit_store.record_audit_event(_actor(), "file.edit", _resource("file-4"), {})

    assert listed["revision"] == 3
    assert fourth["revision"] == 4
    assert all(
        (audit_store.AUDIT_DIR / relative).read_bytes() == payload
        for relative, payload in sealed_before.items()
    )

    for path in sealed_segments:
        path.chmod(0o600)
        path.unlink()
    with pytest.raises(audit_store.AuditIntegrityError, match="old segment is not sealed"):
        audit_store.list_audit_events("ws-audit")


def test_r4_raw_pseudonym_shaped_ids_are_hmac_pseudonymized_again() -> None:
    raw_workspace = "ws_" + "a" * 40
    raw_resource = "res_" + "b" * 40

    event = audit_store.record_audit_event(
        _actor(),
        "file.create",
        {"workspace_id": raw_workspace, "resource_type": "file", "resource_id": raw_resource},
        {},
    )
    response = audit_store.list_audit_events(raw_workspace)

    assert event["workspace_id"] != raw_workspace
    assert event["resource_id"] != raw_resource
    assert event["workspace_id"] == audit_store._pseudonymize_workspace_id(raw_workspace)
    assert event["resource_id"] == audit_store._pseudonymize_resource_id(raw_resource)
    assert response["workspace_id"] == event["workspace_id"]


def test_r4_production_contract_requires_sealed_container_without_protected_append() -> None:
    service = {
        "isVersioningEnabled": True,
        "deleteRetentionPolicy": {"enabled": True, "days": 30},
        "containerDeleteRetentionPolicy": {"enabled": True, "days": 30},
    }
    active_policy = {"state": "Locked", "allowProtectedAppendWrites": True}
    active_container = {
        "hasLegalHold": True,
        "legalHold": {
            "hasLegalHold": True,
            "tags": [{"tag": "dataforgeaudit"}],
            "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": True},
        },
    }
    sealed_policy = {"state": "Locked", "allowProtectedAppendWrites": False}
    sealed_container = {
        "hasLegalHold": True,
        "legalHold": {
            "hasLegalHold": True,
            "tags": [{"tag": "dataforgeaudit"}],
            "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": False},
        },
    }

    audit_store._validate_production_contract(
        service, active_policy, active_container, "dataforgeaudit", sealed_policy, sealed_container
    )
    for broken_policy, broken_container in [
        ({**sealed_policy, "allowProtectedAppendWrites": True}, sealed_container),
        (
            sealed_policy,
            {
                **sealed_container,
                "legalHold": {
                    **sealed_container["legalHold"],
                    "protectedAppendWritesHistory": {"allowProtectedAppendWritesAll": True},
                },
            },
        ),
    ]:
        with pytest.raises(audit_store.AuditPersistenceError, match="sealed|contract"):
            audit_store._validate_production_contract(
                service, active_policy, active_container, "dataforgeaudit", broken_policy, broken_container
            )


def test_r4_terraform_seals_segments_and_requires_irreversible_lock_confirmation() -> None:
    storage = (audit_store.ROOT / "infra" / "modules" / "storage" / "main.tf").read_text(encoding="utf-8")
    environment = (audit_store.ROOT / "infra" / "envs" / "dev" / "variables.tf").read_text(encoding="utf-8")
    example = (audit_store.ROOT / "infra" / "envs" / "dev" / "terraform.tfvars.example").read_text(encoding="utf-8")
    readme = (audit_store.ROOT / "README.md").read_text(encoding="utf-8")

    assert 'resource "azurerm_storage_container" "audit_sealed"' in storage
    assert 'resource "azurerm_storage_container_immutability_policy" "audit_sealed"' in storage
    assert "protected_append_writes_enabled       = false" in storage
    assert 'resource "azapi_resource_action" "audit_sealed_legal_hold"' in storage
    assert "allowProtectedAppendWritesAll = false" in storage
    assert "audit_immutability_lock_confirmation" in environment
    assert 'audit_immutability_lock_confirmation = "LOCK_DATAFORGE_AUDIT_WORM"' in example
    assert "var.audit_immutability_locked" in storage
    assert 'var.audit_immutability_lock_confirmation == "LOCK_DATAFORGE_AUDIT_WORM"' in storage
    assert "precondition" in storage
    assert "irreversible" in readme.lower()


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
