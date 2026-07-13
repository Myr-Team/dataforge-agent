# P2-A Task 6 Report: Durable Connector Records And Key Vault Adapter

## RED / GREEN

- RED: `python -m pytest tests/test_connector_store.py tests/test_connector_secret_store.py tests/test_data_workbench_connectors.py -q` failed with missing durable connector and secret-store modules.
- GREEN: the focused suite passed after the implementation, including Key Vault configuration behavior, session expiry, opaque references, durable Blob record recovery, reconnect after process-state clearing, safe delete recovery, and safe sync lineage.

## Secret isolation

- Connector records contain only connector identity, status, persistence, safe metadata, and an opaque `secret_ref`.
- Passwords, usernames, connection strings, SAS values, and tokens remain inside `SecretStore` only. API responses omit `secret_ref` and credential fields.
- Task results use the existing task-store allowlist and contain only durable ids such as `ingest_job_id`.
- SQL error logging redacts credential key/value forms and user names. Connector delete records `delete_pending` before secret deletion so partial failure is visible and retryable.

## Persistence and reconnect

- With `DF_KEY_VAULT_URL`, `KeyVaultSecretStore` constructs `DefaultAzureCredential` and `azure-keyvault-secrets` `SecretClient`. Initialization or Key Vault failures are surfaced; there is no fallback to session storage.
- Connector records use strict Blob JSON persistence when Blob storage is configured, allowing a new process/instance to recover a redacted record. Local files are a replica only in that mode.
- Without `DF_KEY_VAULT_URL`, the encrypted process-local store reports `persistence=session_only`; records become `expired` after TTL or process clearing and cannot reconnect without new credentials.
- Reconnect retrieves the secret only server-side and never returns it.

## API samples (redacted)

```json
{
  "connector_id": "sql_7f2a...",
  "connection_id": "sql_7f2a...",
  "kind": "sql",
  "status": "connected",
  "persistence": "key_vault",
  "metadata": {"server": "sql.example", "database": "sales", "access": "read_only"}
}
```

```json
{
  "connector_id": "sql_7f2a...",
  "syncing": false,
  "source": {"kind": "sql", "table": "dbo.sales", "observed_at": "2026-07-13T00:00:00+00:00", "source_rows": 100, "column_hash": "2d711642b726b04401627ca9fbac32f5"},
  "task": {"task_type": "connector.sql.sync", "result": {"ingest_job_id": "job_..."}}
}
```

## Verification

- Focused connector/data/task safety regression: 58 passed before final remote-record extension; final focused connector suite: 9 passed.
- Full Python suite: `369 passed, 1 warning`.
- Import smoke: `python -m backend.import_smoke` passed.
- Node tests: 20 passed across the three existing `.test.mjs` files.
- Frontend production build: `npm run build` passed.
- Diff check: `git diff --check` passed.

## Self-check

- SQL keeps allowlisted table lookup and quoted identifiers; Blob keeps account/container/blob validation and size limits. Both paths remain read-only.
- Sync creates a durable ingest task, imports a new workspace file rather than overwriting the source, and records connector/table/blob plus cursor/watermark lineage.
- Disconnect keeps record and secret; delete removes both, while partial delete failures remain explicit and recoverable.
- UI shows durable/session-only/expired/disconnected/syncing/error state and offers reconnect, sync, disconnect, and delete without refilling credentials.

## Concerns

- Deployments using `DF_KEY_VAULT_URL` require the managed identity to have Key Vault secret get/set/delete permissions. Missing permission intentionally fails the connector operation rather than falling back to session-only storage.
- Session-only connectors are intentionally non-recoverable after a process restart or TTL expiry; the UI exposes this as expired.

## Security Review Follow-up

### RED / GREEN

- RED reproduced client supplied `cursor`/`watermark` reflection attempts containing bearer, signature, password, and URI payloads; deterministic reference and record-identity tests also failed before the follow-up.
- GREEN rejects either client field with HTTP 422 before connector/source access. Lineage now contains only server-derived observed time, typed row/byte counts, and a hash of validated discovered column names.
- GREEN binds Key Vault and session references deterministically to trusted workspace and connector identities; load, get, and delete fail closed for forged or cross-workspace references.

### Lifecycle and safety updates

- Sync creates and claims its durable task before setting `syncing` and before secret/source access. It records the ingest job id before work starts, ends task and connector state consistently, and requests a forced new workspace file version.
- Session-only secrets missing in another instance are projected as expired for that response without mutating the durable record.
- Connector records now carry revisions and delete phases (`deleting`, `secret_deleted`); failures remain explicit and retryable.
- Connector API errors use stable codes instead of free-form exception messages. SQL logging records category and exception type only.
- Key Vault health reports `configured_unverified` after token/client construction and only real connector operations establish usable access.

### Follow-up verification

- Focused security and lifecycle suite: `38 passed`.
- Full Python suite: `380 passed, 1 warning`.

### Second review follow-up

- Key Vault and ConnectorStore now share `expected_secret_reference()` rather than independent string formats; integration coverage uses the real KeyVaultSecretStore with a fake Azure SecretClient.
- `DF_KEY_VAULT_URL` accepts only an https host without path, user info, query, or fragment. Health emits only the canonical scheme and host.
- The full test suite still contains one pre-existing experimental workflow warning; it is not treated as a connector health success signal.
