# Budget Task 2 Report

## Scope

- Added an internal, trusted Easy Auth workspace-member loader in `control_plane.py`.
- Added `MemberDirectory` to convert tenant + actor IDs to FinOps HMAC actor references before producing administrator-only member records.
- Added `MemberCostReader` with a strict UTC calendar-month boundary contract and truthful USD spend, unpriced-count, coverage, status, and freshness projection.
- Added tenant-scoped request-event aggregation plus a deterministic primary-model query to `SqlMemberBudgetRepository`.

## TDD evidence

- RED: `python -m pytest tests/test_finops_member_directory.py tests/test_finops_member_budget_sql.py tests/test_ui_truthfulness_contract.py -q` failed because `backend.finops.member_directory` was absent.
- RED: after locking the reader to the requested three-argument interface, its focused test failed with the expected argument-count error.
- GREEN: the focused suite passed after implementation; final focused identity, aggregation, SQL-repository, and query checks passed `50 passed`.

## Scope and safety checks

- No route, Graph lookup, deployment, UI, or email work was added.
- Raw Entra identifiers are confined to the internal loader and are converted to `actor_ref` before member records are returned.
- SQL never coalesces an absent `cost_amount` to zero; unpriced-only actor spend remains unavailable.
- The SQL table is the reconciliation source of truth; request-event deduplication remains the existing ingestion/reconciliation responsibility.
