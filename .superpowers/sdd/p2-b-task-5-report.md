# P2-B Task 5 Remediation Report

Status: `DONE`

Base committed task: `3738721` (`feat: persist immutable workspace audit events`)

## Remediation Summary

All nine review findings were remediated without reverting the interrupted worker's changes, changing Easy Auth configuration, or touching `output/`.

| Finding | Remediation | Binding coverage |
| --- | --- | --- |
| 1. Protected monotonic production anchor and rollback/delete detection | Replaced mutable ledger snapshots with segmented append streams plus a global signed monotonic anchor stream. Production verifies Blob versioning, blob/container soft delete, a locked time-based immutability policy, and protected append writes through ARM before every audit access. Missing/truncated streams, anchor gaps, event rollback, hash mismatch, and event deletion fail closed. A writer may recover exactly one valid HMAC-chained event beyond an existing anchor after interruption; missing anchors and larger gaps remain integrity failures. | Local delete/rollback, WORM contract matrix, chain tamper, unanchored-head recovery, CAS conflict, and policy-invalid replay tests. |
| 2. No production local fallback; `STORAGE_ACCOUNT_NAME` | Local audit persistence now requires `DF_AUDIT_LOCAL_MODE=1` and is prohibited when production/Container Apps is detected. Production requires configured Blob storage. Blob, workspace, dependency-health, and audit account discovery accept `STORAGE_ACCOUNT_NAME` while retaining `DF_STORAGE_ACCOUNT` as an alias. | Production local-mode rejection, missing durable Blob rejection, and deployed account-name discovery tests. |
| 3. Reserved workspace ID before audit with zero orphan mutation | New uploads generate a collision-resistant workspace ID without creating directories, registry entries, blobs, or upload jobs. The reserved ID is audited before `create_workspace_upload_job`; the store consumes the same ID without append lookup. Existing-workspace uploads remain authorized and audited before mutation. | Audit-outage test proves the workspace root is not created; reserved-ID pass-through and existing-upload fail-closed tests. |
| 4. Strong key ring, key ID, rotation, and Key Vault contract | Audit events and anchors persist `key_id`; the active key comes from a JSON key ring of base64 values that must decode to at least 32 bytes. Historical keys verify old records during rotation. Production/Blob mode rejects missing or malformed rings. Terraform accepts only a versionless Key Vault secret URI, creates a user-assigned identity, grants `Key Vault Secrets User` on the supplied vault resource ID before app creation, and injects the ring through a Container Apps secret reference. No key value enters Terraform variables or source. | Rotation with retained old key, short-key rejection, missing production ring, key ID validation, and static infra contract test; `terraform validate` also passes. |
| 5. Client-controlled dedup masking repeated mutations | Audit event IDs are server-generated UUIDs for each call. Reusing request/task/correlation IDs records another mutation attempt. A single server-side CAS retry keeps its in-memory event ID only to avoid duplicating that same append attempt. | Reused request ID test asserts distinct IDs and three revisions. |
| 6. Truthful task terminal audit after CAS | Terminal requests emit a non-terminal `task.transition` attempt before persistence. `task.complete`, `task.cancel`, or `task.fail` is selected from the state actually returned by successful Blob CAS (or local persistence), not from the requested candidate. CAS conflicts and persistence failures emit transition failures without a false terminal event. | Successful terminal lifecycle, CAS conflict, CAS exception, and committed-state mismatch tests, including explicit red/green proof. |
| 7. Workspace authz and audit for PDF/image/narration | All three endpoints require `workspace_id`, a trusted tenant identity, workspace `artifact.generate` permission, and a required audit write before invoking PDF, image, or narration generation. Unauthenticated, denied, and audit-unavailable requests cannot run the tool. | Parameterized 422/401/403/503 tests plus authorized success tests asserting the exact workspace/action/resource audit event. |
| 8. Truthful experiment promotion hook | The future promotion hook requires an explicit `attempt`, `succeeded`, or `failed` phase. It records `promotion_attempt`, `experiment_promoted`, or `promotion_failed` with matching result and rejects unknown phases. | Phase mapping, safe correlation, and invalid-phase tests. |
| 9. Local path traversal rejection | Workspace IDs now use a strict path-free grammar before authorization/audit lookup and workspace creation. Reserved/requested workspace IDs reject separators and traversal. Audit local stream paths reject absolute, drive-qualified, UNC, empty, dot, and escaping paths before filesystem access. | Workspace ID traversal matrix, workspace-store pre-mutation rejection, and absolute local stream path tests. |

## Preserved Contracts

- Audit persistence remains privacy-minimized: raw actor identity is HMAC-pseudonymized; prompts, file content, credentials, provider bodies, and arbitrary metadata are not schema fields.
- Event action/resource/result/reason/correlation values remain strictly allowlisted and fully revalidated on read.
- Blob append positions and task updates retain conditional compare-and-swap behavior.
- Workspace authorization remains fail closed; denial audit failure never grants access.
- The governance endpoint remains owner/admin-only with bounded opaque-cursor pagination and truthful read-only permissions.
- No `update_audit_event` or `delete_audit_event` interface exists.
- No Easy Auth or authentication deployment configuration changed.
- `output/` remains untracked and untouched.

## Test Evidence

### Interrupted-state assessment

```text
python -m pytest tests/test_audit_store.py tests/test_actor_audit_usage.py tests/test_task_store.py -q
76 passed in 16.56s
```

This showed that the stalled worker's tests passed but did not cover committed-state mismatch, pre-authorized Key Vault identity, interrupted anchoring recovery, successful artifact audit, invalid experiment phases, or absolute local paths.

### Strengthened red/green evidence

The first strengthened run produced `4 failed, 3 passed in 5.05s`: valid unanchored recovery, the Key Vault identity contract, and invalid experiment phase failed for the intended missing behavior; the task case exposed an invalid queued-to-completed test setup and was corrected to queued-to-failed before binding the behavior.

The corrected task regression was then explicitly proven against the pre-fix terminal selection:

```text
1 failed in 0.27s
assert ['task.transition', 'task.fail'] == ['task.transition']

1 passed in 0.09s
```

The strengthened remediation selection passed:

```text
11 passed in 4.04s
```

### Focused remediation suites

```text
python -m pytest tests/test_audit_store.py tests/test_actor_audit_usage.py tests/test_task_store.py -q
86 passed in 17.60s
```

The broader audit/task/artifact/workspace/Blob/connector/outcome/experiment sweep passed:

```text
227 passed in 55.88s
```

### Full repository

```text
python -m pytest -q
611 passed, 1 warning in 73.92s (0:01:13)
```

The warning is the existing `ExperimentalWarning` from `backend/maf_team_runtime.py` exercised by `tests/test_maf_evaluation_contract.py`.

## Mechanical Verification

```text
python -m compileall -q backend tests
exit 0

import check
imports=ok routes=ok append_only_api=ok

terraform fmt -check -recursive ../..
exit 0

terraform validate -no-color
Success! The configuration is valid.

git diff --check
exit 0
```

The import check verifies the governance, PDF, image, and narration routes and confirms that audit update/delete symbols do not exist.

## Deployment Contract

Before applying production infrastructure, provide:

- `audit_immutability_locked=true` after reviewing the irreversible retention lock.
- `audit_hmac_active_key_id` matching an entry in the Key Vault JSON key ring.
- `audit_key_vault_id` for an RBAC-enabled vault.
- A versionless `audit_hmac_keyring_secret_uri` whose secret value is the JSON key ring.

Retain old ring entries while old events reference them. Container Apps uses the versionless reference for secret rotation; the backend rejects a ring that omits a key needed by persisted history.
