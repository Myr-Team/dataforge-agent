# Task 3: Bedrock API Contract, Audit, and Feature Gate

## Scope delivered

- Added discriminated `deepseek` / `aws_bedrock` create and rotate contracts.
- Bedrock derives its control-plane endpoint server-side and serializes its
  credential bundle only as `secret_value` for the provider service.
- Bedrock create, test, and rotate require both provider connector flags;
  normal provider operations still require the global connector flag.
- Provider validation responses are generic so AWS credential values are never
  returned in validation details. Audit resources contain only provider ID,
  type, display name, and region.
- Mutation handlers perform a read-only revision preflight before writing an
  audit event, so deterministic stale-revision 409s do not change audit,
  registry, or secret state. Successful mutations retain audit-before-mutation.
- DeepSeek rotation remains compatible with its existing body that omits
  `provider_type`.

## TDD evidence

- RED: `python -m pytest tests/test_model_provider_api.py tests/test_model_provider_audit.py -q`
  produced `3 failed, 8 passed`: Bedrock requests were rejected as DeepSeek-only
  validation errors. A separate credential-redaction test failed because the
  previous FastAPI validation response echoed the request body.
- GREEN: the same focused command passed `15 passed` after implementation.

## Verification

- `python -m pytest tests/test_aws_bedrock_provider.py tests/test_model_provider_api.py tests/test_model_provider_audit.py tests/test_model_provider_repository.py tests/test_model_provider_secrets.py tests/test_model_providers.py -q`
  -> `40 passed`.
- `python -m compileall -q backend` -> exit 0.
- `git diff --check` -> exit 0.

## Scope retained

- Runtime provider routing, APIM provisioning, and FinOps actions remain
  disabled; the environment example keeps their relevant flags off.

## Blocking review remediation

### Root cause

- Revision preflight, durable audit, and the service mutation were separate
  critical sections. The repository CAS protected only the registry row, so a
  competing request could advance the revision after preflight while the first
  request had already appended audit or written a replacement secret.
- The Bedrock-specific connector check was present on create, test, and rotate,
  but PATCH and disable performed provider writes after checking only the global
  provider connector flag.

### Design and implementation

- Added a provider-scoped `mutation_guard(tenant_ref, provider_id)` repository
  contract.
- The SQL repository obtains a SQL Server session-owned exclusive application
  lock on a deterministic SHA-256-derived provider key. The dedicated
  connection remains open across preflight, feature-gate evaluation, durable
  audit, and the complete service mutation, then releases the lock and closes
  the session.
- The in-memory repository uses a process-wide keyed `RLock` registry shared
  across repository instances. Entries are reference-counted and removed after
  the final holder exits.
- Create, test, rotate, PATCH, and disable now use the same repository instance
  and guard for their full mutation critical section. Revisioned losers acquire
  the guard after the winner, fail the fresh preflight with HTTP 409, and do not
  append audit, rotate a secret, or update the registry.
- PATCH and disable now apply the Bedrock-specific gate after the guarded
  provider lookup and before audit or mutation. The existing global gate remains
  enforced by request context.

### TDD evidence

- RED:
  `python -m pytest tests/test_model_provider_api.py -k "bedrock_patch_is_hidden or bedrock_disable_is_hidden or competing_revisioned_rotations" tests/test_model_provider_repository.py -k "mutation_guard or bedrock_patch_is_hidden or bedrock_disable_is_hidden or competing_revisioned_rotations" -q`
  -> `6 failed, 18 deselected`.
  - Bedrock PATCH and disable returned HTTP 200 while the specific flag was off.
  - The competing rotation did not observe a guard.
  - Neither repository implementation exposed `mutation_guard`.
- GREEN: the same focused command -> `6 passed, 18 deselected`.
- The deterministic competing-rotation test blocks the first request in durable
  audit while the second attempts the same provider guard. After release, the
  first succeeds and the second returns HTTP 409; assertions prove exactly one
  new audit event, one secret rotation, and only the successful writer's two
  expected registry updates.
- Repository tests prove same-provider in-memory guards serialize across
  repository instances, and the SQL guard holds the same session application
  lock resource until release/connection close while rejecting failed lock
  acquisition.

### Verification

- Required minimum:
  `python -m pytest tests/test_model_provider_api.py tests/test_model_provider_audit.py tests/test_model_provider_repository.py tests/test_model_provider_secrets.py -q`
  -> `33 passed in 5.27s`.
- Broader Bedrock/provider suite:
  `python -m pytest tests/test_aws_bedrock_provider.py tests/test_model_provider_api.py tests/test_model_provider_audit.py tests/test_model_provider_repository.py tests/test_model_provider_secrets.py tests/test_model_providers.py -q`
  -> `46 passed in 5.06s`.
- `python -m compileall -q backend` -> exit 0.
- `git diff --check eed7e4b20ff1df2801d3d4687d2e973052ea1d50..HEAD`
  -> exit 0.

### Changed files

- `backend/model_provider_repository.py`
- `backend/model_provider_router.py`
- `tests/test_model_provider_repository.py`
- `tests/test_model_provider_api.py`
- `.superpowers/sdd/task-3-report.md`

### Self-review

- All five provider mutation handlers acquire the repository guard before any
  provider preflight or audit and hold it through the service mutation.
- Audit remains before mutation for successful requests; stale revision
  conflicts are detected before audit.
- Every Bedrock write requires both the global provider flag and the
  Bedrock-specific flag. DeepSeek request compatibility is unchanged.
- No credential fields were added to API responses, logs, audit metadata, or
  repository records.
- Runtime routing, APIM, pricing, FinOps metrics/actions, auth, and environment
  defaults were not changed.
- Pre-existing unrelated dirty and untracked workspace files were left
  untouched and are excluded from the remediation commit.

### Validation boundary

- SQL application-lock acquisition and session lifetime are covered with a
  pyodbc-style connection test double; this remediation did not run a live
  two-session Azure SQL contention test.
