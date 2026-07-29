# Budget Task 2 Report

## Scope

- Added an internal, trusted Easy Auth workspace-member loader in `control_plane.py`.
- Added `MemberDirectory` to convert tenant + actor IDs to FinOps HMAC actor references before producing administrator-only member records.
- Added `MemberCostReader` with a strict UTC calendar-month boundary contract and truthful USD spend, unpriced-count, coverage, status, and freshness projection.
- Added tenant-scoped request-event aggregation plus a deterministic primary-model query to `SqlMemberBudgetRepository`.

## TDD evidence

- RED: `python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql.py tests/test_ui_truthfulness_contract.py -q` failed because `backend.finops.member_directory` was absent.
- RED: after locking the reader to the requested three-argument interface, its focused test failed with the expected argument-count error.
- GREEN: the initial focused suite passed after implementation. The remediation evidence below records exact, current commands and totals.

## Scope and safety checks

- No route, Graph lookup, deployment, UI, or email work was added.
- Raw Entra identifiers are confined to the internal loader and are converted to `actor_ref` before member records are returned.
- SQL never coalesces an absent `cost_amount` to zero; unpriced-only actor spend remains unavailable.
- The SQL table is the reconciliation source of truth; request-event deduplication remains the existing ingestion/reconciliation responsibility.

## Review remediation design

- `MemberCostReader` and `SqlMemberBudgetRepository` now require authorized workspace IDs. Each independently deduplicates up to 100 IDs; an empty scope returns `{}` before opening a connection. Both aggregate and primary-model queries use the same bound parameter order: tenant, workspace IDs, UTC start, UTC end.
- `MemberDirectory` filters raw trusted identities by exact requested tenant before creating an HMAC actor reference.
- The internal production loader accepts an invited identity only where the latest persisted invitation event is `accepted` and its trusted actor/tenant pair exactly matches the stored member. It does not trust stored display name or email for that path; direct Easy Auth identities retain their controlled metadata.

## Remediation RED/GREEN evidence

- RED: `python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql_repository.py -q` -> `6 failed, 17 passed`; expected missing workspace interface/predicates, cross-tenant filtering, and accepted-invitation provenance.
- RED: `python -m pytest tests/test_finops_member_directory.py -q` -> `1 failed, 5 passed`; expected proof that accepted invitations still exposed mutable stored display/email.
- GREEN: `python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql_repository.py -q` -> `23 passed`.
- Final checks: `python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql.py tests/test_ui_truthfulness_contract.py -q` -> `25 passed`; `python -m pytest tests/test_finops_member_budget_sql_repository.py -q` -> `17 passed`; `python -m compileall -q backend` -> exit 0; and `git diff --check b40fbf88177e439f842f17f2b46c782c8f986209..HEAD` -> clean.

## Remediation changed files and self-review

- Changed: `backend/finops/member_directory.py`, `backend/finops/sql_member_budgets.py`, `backend/control_plane.py`, `tests/test_finops_member_directory.py`, `tests/test_finops_member_budget_sql_repository.py`, and this report.
- Reviewed for: no route/Graph addition, no tenant-wide fallback on empty scope, both SQL queries carry workspace scope, raw IDs never enter public member records, and unrelated worktree changes remain unstaged.

## Canonical invitation-journal remediation

- Root cause: the production loader walked raw `workspace_invitation_events` in array order, bypassing the invitation-store validation that rejects illegal state sequences. A stale accepted event after revoke could therefore be trusted.
- Design: `invitation_store.accepted_invitation_identity()` now reads the canonical validated journal and returns an identity only when the effective state is accepted. The control-plane loader invokes it for stored invitation IDs and returns no invited identities when validation raises `InvitationPersistenceError`.
- RED: `python -m pytest tests/test_finops_member_directory.py -q` -> `2 failed, 7 passed`; stale post-revoke acceptance was projected and the repository Protocol lacked `workspace_ids`.
- GREEN: `python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql.py tests/test_ui_truthfulness_contract.py -q` -> `30 passed`; `python -m pytest tests/test_finops_member_budget_sql_repository.py -q` -> `17 passed`; `python -m compileall -q backend` -> exit 0; `git diff --check` and `git diff --check b40fbf88177e439f842f17f2b46c782c8f986209..HEAD` -> clean.
- Added behavioral coverage for accepted-to-revoked, stale/out-of-order accepted after revoke, malformed journal, pending/unaccepted invitation, and the protocol signature.
