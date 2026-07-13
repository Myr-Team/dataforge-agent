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

## Third Review Remediation

- Role changes now journal against pending and accepted invitations. Replay derives the latest valid role, so downgrade-before-accept and accept-before-downgrade both activate as `viewer`; metadata roles are not used for bootstrap authorization.
- Graph binding now records only sanitized token provenance and resource tenant context. App-only context derives its resource tenant from configured Graph/Azure tenant, and acceptance requires it to match the trusted inviter tenant. Delegated flows bind to the trusted inviter tenant without decoding or persisting JWT fields or tokens.
- Journal role replay uses the canonical actor/tenant identity and validates allowed invited roles. Activation consumes the journal-derived role rather than the workspace metadata member row.
- No token is persisted in invitation state. Resource-tenant mismatch or absent app-only resource tenant leaves the invitation unaccepted.

Third-remediation TDD evidence:

```text
2 failed, 63 passed in 5.85s
65 passed in 5.96s
537 passed, 1 warning in 48.64s
python -m compileall -q ...      # exit 0
imports ok
git diff --check                 # exit 0
```

The warning remains the unrelated `ExperimentalWarning` from `backend/maf_team_runtime.py:1060`.

## Second Review Remediation

### Documented Graph identity binding

- Graph invitation handling now uses only the documented `invitedUser.id` response field. It no longer expects or persists `invitedUser.tenantId`.
- `accept_provider_invitation` receives the authenticated inviter and requires a trusted Easy Auth OID plus tenant ID. The accepted identity is the Graph `invitedUser.id` bound to that trusted inviter tenant.
- Missing or untrusted inviter identity leaves the invitation pending. Provider status, email, and invitation ID alone do not create acceptance or access.

### Journal-authoritative invited membership

- The CAS invitation journal now records activation and role-change lifecycle events. Authorization derives invited-member access from the journal before considering metadata, and ignores metadata rows carrying an invitation ID.
- Metadata remains a display mirror. A stale `workspace_members` write cannot restore an invited member, elevate a role, or override a journal downgrade/removal.
- Reissue/removal revocation considers all effective pending or accepted grants linked by either target email or the accepted OID/tenant identity, preventing alias invitations from reactivating access.

### Failure and schema handling

- Activation catches durable journal persistence/validation errors and denies the current request rather than returning the bootstrap role.
- Journal mutations compare before/after state and skip CAS writes for no-op lookups or identity mismatches.
- Malformed journal documents or events now raise `InvitationPersistenceError`; they are never treated as empty state or overwritten.
- Journal-derived roles validate against `admin`, `editor`, and `viewer`; malformed or legacy `owner` invitation events fail closed.

### Second-remediation TDD evidence

Initial red verification after adding the review regressions:

```text
3 failed, 60 passed in 6.08s
```

The failures demonstrated the undocumented Graph tenant assumption, missing inviter authentication at the provider-acceptance boundary, and metadata-first authorization.

Final focused verification:

```text
63 passed in 5.42s
```

Final full verification:

```text
535 passed, 1 warning in 52.45s
```

The warning is unchanged and unrelated: `ExperimentalWarning` from `backend/maf_team_runtime.py:1060`.

Final compile/import/diff verification:

```text
python -m compileall -q ...      # exit 0
imports ok
git diff --check                 # exit 0
```

## Review Remediation

This follow-up addresses every Critical and Important Task 4 review finding without changing Easy Auth configuration.

### Production identity binding

- `backend.graph_client.send_graph_invitation` exposes only sanitized provider identifiers and, when Graph returns it, the invited user's tenant ID.
- `backend.control_plane.invite_entra_workspace_member` records `accepted` only through `accept_provider_invitation` when the server-side Graph response contains `source=microsoft_graph`, a concrete `invited_user_id`, and a concrete tenant ID. Graph responses without either identity component remain `pending`.
- Provider-bound acceptance is still not authorization: workspace access requires a later trusted Easy Auth login whose OID and tenant exactly match the stored accepted identity.
- The direct trusted transition path continues to require `source=easy_auth`, OID, and tenant. There is no email-only or invitation-ID-only acceptance endpoint.

### Durable append-only journal

- Invitation events now use `workspaces/{workspace_id}/invitation-events.json` as the shared durable journal when Blob storage is configured.
- Every mutation reads the journal revision, applies a pure append-only mutation, and uses Blob ETag/revision CAS. Conflicts reload and retry up to five times; unresolved conflicts raise `InvitationPersistenceError` and fail closed.
- Blob CAS now conditionally creates a missing journal at revision zero. Local metadata remains a development-mode mirror only when Blob storage is not configured.
- Control-plane invitation creation, provider acceptance, failure transitions, revocation, and authorization activation all use the same journal API.

### Single-use bootstrap and current membership authority

- Activation appends an `event_type=activation` consumption marker atomically to the durable journal before persisting the member's active OID/tenant binding.
- A consumed accepted event cannot activate again. On later requests, persisted active membership is resolved first and its current role is authoritative.
- A role downgrade (for example, `admin` to `viewer`) therefore persists and cannot be overwritten by the old accepted invitation. Revoked/removed memberships cannot be recreated by old events.

### Reissue, removal, roles, and production RBAC

- Explicit reissue (`reinvite=true` on the member invite request, or `reissue=True` at the store boundary) revokes every effective pending or accepted invitation for the email before assigning a new invitation ID.
- Member removal revokes every effective invitation for that identity before removing the member. Revocation persistence errors are propagated; removal does not report success or delete the member on failure.
- The invitation store independently allows only `admin`, `editor`, and `viewer`; `owner` and invalid roles are rejected. Activation revalidates the membership role.
- With `DF_WORKSPACE_RBAC_ENFORCED=1`, owner and member resolution requires trusted Easy Auth source, OID, tenant ID, and tenant-scoped identity match. Email fallback is retained only when RBAC is disabled for deliberate local compatibility. Trusted default-owner matching can use `DF_WORKSPACE_OWNER_OID` and `DF_WORKSPACE_OWNER_TENANT_ID`.

### Follow-up TDD evidence

The new remediation tests were written first. The initial red run recorded these expected gaps:

```text
9 failed, 50 passed in 6.18s
```

The failures covered store role validation, provider identity acceptance, CAS retry support, reissue/revocation, strict production RBAC, and one-time activation.

Final focused verification:

```text
60 passed in 5.22s
```

Final full verification:

```text
532 passed, 1 warning in 48.47s
```

The warning remains the unrelated `ExperimentalWarning` from `backend/maf_team_runtime.py:1060`.

Final compile/import/diff verification:

```text
python -m compileall -q ...      # exit 0
imports ok
git diff --check                 # exit 0
```
