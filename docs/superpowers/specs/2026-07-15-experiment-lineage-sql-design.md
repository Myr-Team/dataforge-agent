# Experiment Lineage SQL Design

## Goal

Make Azure SQL the authoritative transactional store for experiment-version lineage, attachment membership, workspace generation, and purge state. Blob Storage continues to retain raw run documents and generated artifacts.

## Scope

This replaces the multi-Blob lineage state machine introduced on `codex/p2-productization`. It does not change Easy Auth, user-facing authorization, evidence scoring, artifact rendering, or Agent Flow UI.

## Chosen Approach

Use a new `df_lineage` database on the existing Azure SQL logical server. The backend Container App connects through its system-assigned managed identity using an access token for `https://database.windows.net/.default`; no SQL password or connection string secret is stored in source code, Blob data, logs, or public APIs.

The deployment prerequisite is a Microsoft Entra administrator on the SQL server and a contained database user for the backend managed identity with only the roles needed by this schema. If that Azure-side prerequisite is not yet configured, the application fails closed for version promotion and attachment writes, while existing Blob-run reads remain available as legacy read-only data.

## Data Model

`workspace_lineage` has one row per workspace and serializes transitions with `rowversion` optimistic concurrency:

- `workspace_id` primary key
- `generation` integer
- `lifecycle_state` (`active`, `purging`, `purged`)
- `next_version_ordinal`
- `row_version` SQL rowversion
- timestamps

`experiment_version` is append-only and has a unique `(workspace_id, generation, ordinal)` constraint. It stores the canonical run ID, the evidence and decision fingerprints, verdict/confidence projection, and creation metadata.

`experiment_attachment` binds a plan or artifact snapshot to exactly one version with a payload SHA-256, source run ID, version kind, and immutable creation time.

`workspace_generation_event` records explicit recreate and purge transitions so an old writer cannot attach to a new generation.

## Transaction Flow

1. An analysis completion starts a SQL transaction and locks the workspace row with `UPDLOCK, HOLDLOCK`.
2. The service verifies its captured generation equals the active workspace generation.
3. It compares normalized decision and source-linked evidence fingerprints with the latest canonical version. Only authoritative observed outcomes and source-linked evidence may promote.
4. A promotion inserts the next ordinal and commits together with the lineage update. A duplicate records no new version.
5. Blob documents are then written as non-authoritative run payloads carrying the committed SQL version ID. If Blob publication fails, the version remains committed and is marked `artifact_state=unavailable`; it is never reinterpreted as another version.

Purge and recreation use the same locked workspace row. Purge increments no generation until its DB transition succeeds; recreation is an explicit transaction that increments generation before accepting any new run. Attachment insertion requires the current active generation and a foreign key to the committed version.

## Read Model

The experiment ledger loads canonical versions and attachments from SQL in ordinal order. It hydrates optional display details from Blob only after matching the SQL canonical run ID and attachment payload hash. Missing or mismatched Blob content produces an `unavailable` detail state without removing or renumbering a committed version.

Legacy Blob-only workspaces are presented as legacy read-only data. A migration command may import only complete, internally consistent histories; otherwise it records an unavailable migration state rather than inventing version membership.

## Security and Operations

- SQL access uses the backend managed identity and least-privilege database roles.
- SQL schema deployment is idempotent and uses parameterized statements only.
- Public APIs expose stable version IDs, bounded lifecycle states, and actor-safe fields. They never expose SQL tokens, connection data, internal rowversion values, or raw identity claims.
- The SQL transactional path emits audit events with version ID, workspace ID, committed state, and actor-safe metadata.

## Acceptance Evidence

- Parallel completions yield exactly one next ordinal.
- A purge/recreate race cannot attach an old-generation run.
- Blob failure cannot renumber or delete an SQL-committed version.
- Attachment payload tampering is hidden from public ledger output.
- Legacy incomplete Blob history remains unavailable rather than becoming a synthetic V1.
- Unit/integration tests run against a disposable SQL-compatible fixture; Azure deployment smoke uses the real managed-identity path after SQL prerequisites are configured.
