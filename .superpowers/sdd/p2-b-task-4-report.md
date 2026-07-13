# P2-B Task 4 Report: Entra Invitation Lifecycle

Base commit: `c02051a`

## Scope and changed files

- `backend/invitation_store.py` (new): append-only invitation event storage, legal transition validation, idempotent retries, per-workspace locks, accepted identity lookup, and provider-field sanitization.
- `backend/control_plane.py`: creates pending events for local and Entra invitations, persists failed Graph attempts even without a member fallback, records revocation on member removal, and carries the invitation ID on a pending member.
- `backend/graph_client.py`: distinguishes missing directory-search permission from invitation capability; reports the required directory permission while preserving exact-email invitation support.
- `backend/workspace_authz.py`: removes email-based pending-member activation. A membership becomes active only when an accepted event's trusted `actor_id` and `tenant_id` exactly match the current Easy Auth actor.
- `tests/test_entra_member_invites.py`: lifecycle, idempotency, concurrency, sanitized failure persistence, and Graph permission regressions.
- `tests/test_workspace_roles.py`: pending-access denial, tenant-scoped accepted activation, and owner/admin member-management authorization regressions.
- `.superpowers/sdd/p2-b-task-4-report.md`: this report.

`backend/identity.py` was reviewed but required no change: its existing `is_trusted_tenant_identity` and canonical tenant-scoped identity checks are used by the new store and authorization path. No Easy Auth or authentication configuration was changed. `output/` was not touched.

## Lifecycle and API contract

Invitation state is persisted in `workspace_invitation_events` in workspace metadata. Each state change appends a new event; existing events are never rewritten.

- States: `pending`, `accepted`, `expired`, `failed`, `revoked`.
- Legal transitions: new -> `pending`; `pending` -> `accepted`/`expired`/`failed`/`revoked`; `accepted` -> `revoked`. Terminal states reject further transitions.
- Idempotency: an equivalent pending retry returns the existing pending event; a repeated accepted transition for the same OID and tenant returns the existing accepted event without appending another event.
- Concurrency: pending creation and workspace mutations are protected by per-workspace locks; concurrent equivalent retry tests append one event.
- Accepted transitions require a trusted Easy Auth identity with both OID and tenant ID. Provider status alone has no authorization effect.
- Authorization: invitation creation/removal continues to use `member.manage`; with RBAC enabled this permits owner/admin and rejects editor. The owner path is covered directly by regression tests.
- Activation: pending members never activate by email. On a trusted Easy Auth request, activation occurs only when the effective event is `accepted` and its stored OID plus tenant exactly equal the request claims. A mismatched OID, mismatched tenant, email-only actor, pending invitation, provider-only acceptance, failed invitation, expired invitation, or revoked invitation receives no workspace role.
- Graph search: `GET /api/workspaces/{workspace_id}/members/entra-users` returns `graph_directory_search_permission_denied` with `User.ReadBasic.All or Directory.Read.All` guidance when directory search is forbidden. Exact-email Graph invitation remains independent and can succeed with invitation permission.
- Invitation API: the existing member invite endpoints retain their shapes. Both fallback and no-fallback Graph failures write `pending -> failed`; no-fallback does not create a workspace member.
- Persistence hygiene: invitation events store only sanitized provider source, IDs, status, status code, and error code. They do not persist Graph access tokens, raw Graph payloads, error bodies, or diagnostic detail.

## TDD evidence

Initial focused baseline before new tests:

```text
42 passed in 4.84s
```

First red run after importing the new store:

```text
ModuleNotFoundError: No module named 'backend.invitation_store'
2 errors in 5.37s
```

Second red run after the store was introduced, before integration:

```text
5 failed, 41 passed in 5.06s
```

The final no-fallback audit test was added before its implementation and failed as expected:

```text
KeyError: 'workspace_invitation_events'
1 failed in 4.44s
```

Final focused verification:

```text
48 passed in 4.37s
```

Final full verification:

```text
520 passed, 1 warning in 44.88s
```

The single warning is pre-existing and unrelated: `ExperimentalWarning` from `backend/maf_team_runtime.py:1060` for `FUNCTIONAL_WORKFLOWS`.

Final static checks:

```text
python -m compileall -q ...      # exit 0
imports ok
git diff --check                 # exit 0
```
