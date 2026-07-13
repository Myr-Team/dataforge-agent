# P2-B Task 5 Remediation Report

Status: `DONE`

Base committed task: `3738721` (`feat: persist immutable workspace audit events`)

Interrupted remediation parent: `5799c3b` (`fix: harden immutable audit remediation`)

## Scope

All nine Task5 findings and all six r2 Critical/Important findings are remediated. The interrupted worker's changes were retained and repaired. No Easy Auth configuration was added or changed, and `output/` was left untouched.

## Original Nine Findings

| Finding | Final behavior |
| --- | --- |
| 1. Protected monotonic/immutable production anchor | Each workspace has append-only `events.jsonl` and signed append-only `anchors.jsonl` streams. Production verifies versioning, blob/container soft delete, locked immutability, and protected append writes. Signed heads and full governance scans detect missing, truncated, rolled-back, deleted, or hash-mismatched state. |
| 2. No production local fallback and account-name support | Production prohibits local mode and requires `STORAGE_ACCOUNT_NAME` or `DF_STORAGE_ACCOUNT`. It also prohibits connection strings, emulators, and account keys for audit writes. |
| 3. Pre-audit reserved workspace ID | New-workspace upload reserves a collision-resistant ID without mutation, audits it, and only then creates upload/workspace state. Audit failure leaves no directory, registry, blob, or job orphan. |
| 4. Strong key ring and Key Vault contract | Events and anchors carry `key_id`; base64 keys must decode to at least 32 bytes; retained old keys validate history after rotation. Terraform uses a versionless Key Vault secret, pre-authorized user-assigned identity, and `Key Vault Secrets User`. |
| 5. No client-controlled event dedup | Every mutation call gets a server-generated event UUID. Reused request/task/correlation values cannot suppress repeated mutation attempts. |
| 6. Truthful task terminal audit | The pre-CAS event is only `task.transition`. A terminal event is selected from the state returned by successful durable CAS. Conflicts, failures, and committed-state mismatch cannot create a false terminal event. |
| 7. Artifact endpoint authz and audit | PDF, image, and narration endpoints require a workspace ID, trusted workspace authorization, and required `artifact.generate` audit before tool execution. |
| 8. Truthful experiment hook | Promotion audit requires explicit `attempt`, `succeeded`, or `failed` phase and maps each to the matching result/reason. |
| 9. Local path traversal rejection | Workspace and local audit stream paths reject separators, traversal, absolute/drive/UNC paths, dot segments, and escapes before filesystem mutation. |

## R2 Remediation

### 1. Snapshot-bound event and anchor CAS

- `_StreamSnapshot` carries the bounded tail, validated head, exact byte length, and exact ETag from one stream read.
- Event revision/hash construction uses that snapshot head, and append uses that same snapshot's length and ETag. There is no second position read.
- Anchor construction and append use the same contract.
- Blob append sends both `appendpos_condition` and `IfNotModified` ETag. Local append validates its snapshot before and after opening the descriptor.
- Conflicts reload, revalidate, rebuild, and retry. Head pairs are read in write order (anchor then event); an inconsistent pair is reconfirmed and retried only if either exact snapshot changed.
- Deterministic tests interleave competing event writes, anchor writes, and cross-stream head advancement.

### 2. WORM proof bound to the write account

- Production accepts only the managed-identity URL for the selected `STORAGE_ACCOUNT_NAME`/`DF_STORAGE_ACCOUNT`.
- `DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID` is parsed as a Storage Account ARM ID.
- Its account, subscription, and resource group must match the actual write account plus `DF_AUDIT_STORAGE_SUBSCRIPTION_ID` and `DF_AUDIT_STORAGE_RESOURCE_GROUP` before any Blob client is constructed.
- Terraform injects both expected subscription and resource group values.
- Tests prove that policy verification for account A cannot authorize client construction for account B and that the verified account is passed to a managed-identity-only backend.

### 3. Interrupted genesis recovery

- No-anchor recovery is allowed only for one valid signed revision-1 event with the genesis previous hash and exactly one physical record.
- This works for the first-ever workspace and the first event of another workspace.
- One valid event beyond an existing matching anchor is also recoverable.
- Multiple records, larger gaps, rollback, deletion, or hash mismatch fail closed.
- Fault tests interrupt the first anchor append and prove the next mutation anchors the durable event before advancing.

### 4. Correlation privacy

- Every allowlisted correlation value is HMAC-pseudonymized as `corr_<40 hex>` before schema persistence, including internal IDs and accepted request/idempotency/correlation headers.
- Arbitrary metadata remains discarded; actor identity remains HMAC-pseudonymized.
- JWT-like, API-key-like, and connection-string-like inputs are absent from local bytes and governance API responses.
- Correlation values are never used as event IDs or deduplication keys.

### 5. Truthful Graph invitation failure

- Graph failure first creates/transitions and durably saves the sanitized invitation journal state as `failed`.
- Only after that save succeeds, `invitation.fail` is appended with `result=failed`, `reason_code=invitation_failed`, and invitation correlation.
- Both fallback and no-fallback paths use the same ordering.
- Provider bodies/messages/tokens are not passed to the audit record; persisted provider state retains only source, status, and error code.

### 6. O(1) mutation gate and bounded ARM cache

- Mutation reads Blob properties and at most a 64 KiB tail for each workspace event/anchor stream; records are capped at 16 KiB.
- The normal mutation gate performs no listing and no full-history download. A 300-event test asserts zero full/legacy reads and at most eight snapshot calls.
- Explicit governance reads still perform full event/anchor chain validation.
- Verified production immutability capability is cached for 60 seconds, keyed by exact ARM resource ID, write account, and container.
- Expiry forces ARM revalidation; a changed/unavailable policy fails closed and is not recached. The TTL is the maximum interval during which a policy change can remain masked.

## Preserved Contracts

- Audit action/resource/result/reason/correlation schemas remain allowlisted and are revalidated on read.
- Actor identity, content, prompts, credentials, provider bodies, and arbitrary metadata are not persisted.
- Authorization and required mutation audit remain fail closed.
- Governance pagination remains bounded, opaque-cursor, owner/admin-only, and read-only.
- No audit update/delete interface exists.
- No Easy Auth deployment configuration changed.
- `output/` remains untracked and untouched.

## Test Evidence

Initial interrupted-state focused run:

```text
105 passed in 10.62s
```

Strengthened r2 red selection before implementation:

```text
15 failed, 1 passed, 105 deselected in 6.36s
```

Final focused audit/endpoint/invitation suites:

```text
python -m pytest tests/test_audit_store.py tests/test_actor_audit_usage.py tests/test_entra_member_invites.py -q
125 passed in 8.05s
```

Final full repository suite:

```text
python -m pytest -q
631 passed, 1 warning in 68.78s (0:01:08)
```

The warning is the existing `ExperimentalWarning` from `backend/maf_team_runtime.py` in `tests/test_maf_evaluation_contract.py`.

## Mechanical Verification

```text
python -m compileall -q backend tests
exit 0

import check
imports=ok routes=ok append_only_api=ok

terraform fmt -check -recursive infra
exit 0

terraform validate -no-color
Success! The configuration is valid.

git diff --check
exit 0
```

## Production Configuration

Required production settings are:

- `STORAGE_ACCOUNT_NAME` or `DF_STORAGE_ACCOUNT`
- `DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID`
- `DF_AUDIT_STORAGE_SUBSCRIPTION_ID`
- `DF_AUDIT_STORAGE_RESOURCE_GROUP`
- `DF_AUDIT_CONTAINER`
- `DF_AUDIT_HMAC_ACTIVE_KEY_ID`
- `DF_AUDIT_HMAC_KEYS` from the versionless Key Vault secret reference

Production audit writes reject `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_KEY`, and `DF_STORAGE_KEY`. Retain every historical key-ring entry still referenced by persisted events or anchors.
