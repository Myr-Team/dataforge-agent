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
- Sync creates a durable ingest task, imports a new workspace file rather than overwriting the source, and records connector/table/blob plus server-derived observation metadata only; it never records client cursor/watermark input.
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

## Backend Closure: CAS, Recovery, And Terminal Ordering

### RED / GREEN

- RED reproduced an overwrite race where two local ConnectorStore instances both wrote revision 2, remote connector updates accepted overwrite writes, a `secret_deleted` record attempted a second secret delete, and a claimed sync task could remain running if the `syncing` transition failed.
- GREEN adds real revision CAS for configured Blob storage: the helper reads the Blob ETag and record revision, uploads/deletes with `IfNotModified`, and returns the stable `connector_conflict` code on conflict. Local records use per-record cross-process `.lock` sidecars and UUID temporary names; separate spawned processes now serialize revisions 2 then 3.
- GREEN retains the named Blob path on strict list. Every listed payload must match `connectors/<workspace>/<connector>.json`; a renamed or cross-identity record fails closed.
- GREEN drives deletion through `deleting -> secret_deleted -> record_deleted`. Secret-missing/soft-deleted outcomes are idempotent success; a record-delete failure keeps `secret_deleted` for retry and cannot cause a second secret delete. Missing records are idempotent success.
- GREEN encloses every action after durable task claim in one failure boundary. A failed `syncing` write makes the task terminal `failed`; ingest is intentionally not marked `completed` until the forced-new-version upload, server-derived lineage/history, and connector `connected` transition all succeed. Earlier failure leaves a terminal `failed`/`partial` task and truthful connector error state.

### Secret Isolation And Stable Errors

- Key Vault `put/get/delete` and construction now translate operational failures to stable connector codes with `raise ... from None`; raw endpoint, exception message, password, SAS/signature, URI, bearer, and username values do not enter connector APIs or logs.
- Session-only reconnect with no local secret produces only an ephemeral `{status: "expired", requires_credentials: true}` projection. The durable record remains unchanged for another instance.
- Blob/SQL/Key Vault lifecycle APIs use `{category: "connector", code: "..."}` responses. Regression coverage injects `sig` and password text and confirms it is absent from the HTTP response.

### Corrected API Sample

`cursor` and `watermark` are not client inputs or persisted lineage. Sync rejects either field with 422. The only observed metadata is server-derived and typed:

```json
{
  "connector_id": "sql_7f2a...",
  "syncing": false,
  "source": {
    "kind": "sql",
    "table": "dbo.sales",
    "observed_at": "2026-07-13T00:00:00+00:00",
    "row_count": 100,
    "column_hash": "2d711642b726b04401627ca9fbac32f5"
  },
  "task": {"task_type": "connector.sql.sync", "result": {"ingest_job_id": "job_..."}}
}
```

Warning: `row_count` is the server-observed import preview count; it is not a client-provided cursor or watermark and does not establish a source-system checkpoint.

### Verification

- Focused connector/data/task/security/roles regression: `104 passed`.
- Full Python suite: `393 passed, 1 warning` (existing experimental workflow warning).
- Import smoke: `python -m backend.import_smoke` passed.
- Diff check: `git diff --check` passed.

### Concerns

- A Key Vault soft-deleted secret is treated as already removed during connector deletion. Recovering a connector after deletion still requires a new connect flow and credentials.
- Blob CAS retries are intentionally bounded; a continuously contended record returns `connector_conflict` rather than overwriting a newer lifecycle state.

## Frontend Closure: Server Connector Truth

### RED / GREEN

- RED: no connector view-model existed. The new Node test initially failed with `ERR_MODULE_NOT_FOUND`; it now exercises the real view-model module rather than searching component source strings.
- GREEN: workspace refresh clears connector records immediately, stamps the request sequence and workspace id, and rejects a slow `ws-a` response after `ws-b` becomes current.
- GREEN: `connectorRecords` is the authoritative durable list. `connectorResult` is now a derived selected view merged only with ephemeral per-record source listings; sessionStorage restoration has no connector id and cannot create or replace a durable record.

### Interaction Safety

- Each server record has independent `pending` and `error` state keyed by `connector_id`, and the cards expose select, reconnect, sync, disconnect, and delete actions for both SQL and Blob records.
- The SQL/Blob type cards now select from the matching durable server records. Sync opens the existing table/container/blob selection flow for that record, marks it syncing immediately, and calls the durable connector sync endpoint.
- Reconnecting an expired session selects the record and opens an empty credential form. No password, connection string, SAS, token, or username value is held in the view-model or passed back into form fields.

### Frontend Verification

- Node behavior tests: `24 passed` across `connectorViewModel`, governance, MAF view-model, and task center suites.
- Production build: `npm run build` passed.
- Diff check: `git diff --check` passed.

## Third Review Closure: Finalizing and Action Races

### RED / GREEN

- RED: a completed ingest could expose `connected` before its durable task was committed, and a task CAS conflict could leave the two records inconsistent.
- GREEN: sync now writes `finalizing` with an internal pending task id and sync token only after file version, lineage, and history complete. It verifies that the task update returned `completed` before a CAS transition to `connected`.
- GREEN: list, status, and reconnect recover `finalizing`: a completed task is finalized to connected; running work remains finalizing; failed, partial, or cancelled work becomes a truthful connector error.
- GREEN: a Key Vault secret missing during syncing marks a durable record `error` with `connector_secret_expired`; session-only expiry remains only an ephemeral projection.
- GREEN: all durable sync task failures use `{ "category": "connector", "code": "connector_task_unavailable" }` at the API boundary.
- GREEN: connector records are refreshed only from the guarded server list. Client action state is isolated in the per-connector action map, and action epochs reject late workspace or superseded action responses.

### Additional Verification

- Focused connector/data/task regression: `50 passed`.
- Full Python suite: `399 passed, 1 warning`.
- Import smoke: `python -m backend.import_smoke` passed.
- Node behavior suites: `25 passed`.
- Production build and `git diff --check`: passed.

### Concerns

- A process interruption while the task is still running deliberately exposes `finalizing` rather than guessing that the connector is connected; normal task completion is recovered on the next list, status, or reconnect access.

## Fourth Review Closure: Recoverable Finalization

### RED / GREEN

- RED: finalizing recovery accepted any completed task and could not complete a validated running task after a process crash.
- GREEN: recovery validates the pending task workspace, `connector.manage` action, connector-kind task type, and `ingest_job_id` workspace ownership before completing a running task and CAS-finalizing the connector to connected.
- GREEN: a missing pending task becomes only that connector's `sync_task_missing` error. Task or ingest identity mismatch becomes `sync_task_mismatch`; list continues to return unrelated records, while real `TaskPersistenceError` remains a 503 path.
- GREEN: both connector-item and external-source imports use action epochs; guarded file reloads fetch data without committing groups or storage until the current workspace guard succeeds.
- GREEN: create-connect catch/finally honor the same guard, so a late workspace A request cannot toast or clear workspace B's kind-scoped busy state.
- GREEN: `finalizing` is rendered as `正在完成同步`, is syncing-like in the view model, and reconnect refreshes rather than reconnecting it.

### Verification

- Focused connector/data/task regression: `53 passed`.
- Full Python suite: `402 passed, 1 warning`.
- Node behavior suites: `27 passed`.
- Production build, import smoke, and `git diff --check`: passed.

### Concerns

- Finalization recovery deliberately refuses a task whose durable ownership evidence is incomplete or mismatched. It leaves a stable connector error rather than publishing an unverified connection state.
