# Task 4 Report: Persist And Expose Remediation Drafts

## Status

Implemented and verified Task 4 only. Remediation drafts now have additive SQL
persistence, owner/admin APIs, safe public projections, and authorized
workspace summaries in the risk decision. Promotion creates a governance action
that remains in `draft`; no submit, approve, execute, deployment, traffic, auth,
or `DF_FINOPS_ACTIONS_ENABLED` mutation was added.

## RED evidence

- Migration RED:
  `python -m pytest tests/test_finops_sql_migration.py::test_finops_schema_contains_remediation_tables -q`
  failed because `df_finops.remediation_draft` and
  `df_finops.remediation_transition` did not exist.
- Repository RED:
  `python -m pytest tests/test_finops_remediation_sql.py -q`
  failed during collection because `backend.finops.sql_remediation` did not
  exist.
- API RED:
  `python -m pytest tests/test_finops_remediation_api.py -q`
  produced 12 expected failures because the remediation routes returned 404.
  The missing-opportunity assertion was tightened to require the safe
  `remediation opportunity not found` detail so it could not pass merely
  because the route was absent.

## GREEN and regression evidence

- Initial repository and migration GREEN:
  `6 passed in 2.82s`.
- Initial API GREEN:
  `13 passed in 6.19s`.
- Focused remediation, migration, governance, and decision API run:
  `61 passed in 6.58s`.
- Fresh focused rerun after the final risk-error mapping:
  `61 passed in 7.01s`.
- Full Python suite:
  `1645 passed, 1 skipped, 1 warning in 159.50s`.
- The warning is the known Microsoft Agent Framework experimental workflow
  warning from `tests/test_maf_evaluation_contract.py`.

## Migration and static evidence

- `tests/test_finops_sql_migration.py` passed twice consecutively:
  `17 passed in 0.20s` and `17 passed in 0.19s`.
- `python -m py_compile backend/finops/sql_remediation.py backend/finops/router.py`
  passed.
- Static checks confirmed both remediation tables are guarded by
  `IF OBJECT_ID(...) IS NULL`.
- Static checks confirmed save uses
  `MERGE df_finops.remediation_draft WITH (HOLDLOCK)` and matches tenant,
  draft, workspace, and expected revision before update.
- OpenAPI route inspection found six unique remediation routes and no
  collisions.
- Both request models expose `additionalProperties: false`.
- `git diff --check` passed.

## Changed files

- `backend/finops/sql_remediation.py`
- `backend/sql/finops_schema.sql`
- `backend/finops/router.py`
- `tests/test_finops_remediation_sql.py`
- `tests/test_finops_remediation_api.py`
- `tests/test_finops_sql_migration.py`
- `.superpowers/sdd/operations-roi-risk-task-4-report.md`

## Self-review

- SQL transaction/CAS: save is one locked MERGE plus same-transaction
  transition append using the trusted actor and optional user-provided reason
  supplied explicitly by the service. A stale revision, missing expected row,
  or attempted workspace move produces no MERGE output and maps to
  `RemediationConflict`; database failures roll back and expose only the safe
  persistence error.
- Tenant/workspace authorization: every repository read is tenant scoped;
  save preserves the stored workspace under CAS. API tenant and actor values
  come from trusted request context. Task 3 service authorization filters all
  draft reads/mutations by authorized workspace IDs. Members receive 403;
  cross-tenant and out-of-scope draft reads receive 404.
- Public projection: draft responses exclude `tenant_ref`, `created_by`, and
  `reviewed_by`. Promoted action responses exclude tenant and actor identities,
  transitions, results, provider responses, and internal errors.
- Input boundary: create accepts only `workspace_id`,
  `source_opportunity_id`, and `base_version`; the server reloads the current
  opportunity in the authorized workspace. Script, XML, URL, and resource-ID
  change fields are rejected with 422.
- Promotion safety: promotion returns the promoted draft and deterministic
  tenant-scoped governance action, whose status remains `draft`.
  `DF_FINOPS_ACTIONS_ENABLED` is only read for the response and is not changed.
- Risk decision: the service lists only the requested authorized workspace,
  strips tenant/raw actor fields, and the existing decision projection reduces
  drafts to safe title/summary/status summaries.
- Route review: static collection, detail, review, close, and promote paths are
  uniquely registered; the static collection path is registered before the
  draft-ID path.

## Concerns

- No live SQL Server migration or deployment was performed, as required.
  Migration evidence is repository/static test evidence rather than a
  production database execution.
- Existing unrelated untracked test/workspace artifacts were left untouched.

## Independent review fix: explicit transition audit context

An independent review found that the initial SQL repository inferred a
transition actor from `reviewed_by` or `created_by` and always stored a null
reason. The router accepted `reason`, but the service/repository interfaces did
not carry it.

The fix makes `actor_ref` an explicit repository-save requirement and carries
the optional `reason` through router, remediation service, and repository.
Create, review, close, and both promotion transitions now use the trusted
request actor. The SQL transition insert remains in the same transaction as the
draft CAS write. Actor and reason remain absent from public draft, action, and
risk-decision responses.

### Review-fix RED evidence

- `python -m pytest tests/test_finops_remediation_sql.py
  tests/test_finops_remediation_api.py::test_transition_actor_and_reason_flow_from_trusted_request_to_repository
  -q` produced six expected failures:
  - five SQL tests failed because `save` did not accept `actor_ref`;
  - the API audit assertion showed every actor and reason arriving as null.

### Review-fix GREEN evidence

- New audit-chain plus remediation domain/SQL checks:
  `19 passed in 10.57s`.
- Focused remediation, SQL, API, migration, governance, and decision API run:
  `63 passed in 7.32s`.
- Full Python suite:
  `1647 passed, 1 skipped, 1 warning in 162.31s`.
- The warning remains the known Microsoft Agent Framework experimental
  workflow warning.
- `python -m py_compile backend/finops/remediation.py
  backend/finops/sql_remediation.py backend/finops/router.py` passed.
- `git diff --check` passed.

### Review-fix coverage

- Different creator, reviewer, closer, and promoter identities are asserted at
  the repository boundary.
- Review, close, pending-approval, and promoted transition reasons are
  preserved.
- A stale close does not call repository save or append an audit record.
- A SQL transition-insert failure rolls back the draft CAS transaction.
- Promotion remains draft-only and no deployment, auth, traffic, or action-gate
  change was made.

### Review-fix changed files

- `backend/finops/remediation.py`
- `backend/finops/sql_remediation.py`
- `backend/finops/router.py`
- `tests/test_finops_remediation.py`
- `tests/test_finops_remediation_sql.py`
- `tests/test_finops_remediation_api.py`
- `.superpowers/sdd/operations-roi-risk-task-4-report.md`
