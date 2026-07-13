# P2-B Task 4: Entra Invitation Hardening

## Current Contract

- The invitation journal is append-only and replayed fail-closed. The first state event for each invitation must be `pending`; legal state transitions are `pending -> accepted|expired|failed|revoked` and `accepted -> revoked`.
- An `accepted` event requires a nonempty canonical Easy Auth OID and tenant ID. An activation requires the same nonempty `(actor_id, tenant_id)` pair, follows that accepted event once, and is recorded before access is returned.
- Journal role changes require an existing effective pending or accepted invitation and one of `admin`, `editor`, or `viewer`. The current effective journal role is authoritative for activation and subsequent invited-member access. Workspace-member metadata is only a display mirror.
- A successful Graph invite is accepted only with a sanitized invited-user OID and a trusted inviter tenant binding. Provider status, email, invitation ID, and metadata alone never grant workspace access. Tokens, raw provider payloads, and error bodies are not persisted.
- Durable Blob CAS is the shared mutation path when configured. Journal errors or unresolved conflicts deny the operation; there is no metadata-only authorization fallback.
- Reissue and removal revoke all effective pending or accepted grants for the matching email aliases and canonical OID/tenant identity. Canonical identity matching does not depend on mapping key order.
- Repeating a pending invite with its current effective role returns that existing invitation. Retrying it with an obsolete role fails with `InvitationTransitionError` rather than creating a duplicate or claiming the obsolete role is current; callers must perform an explicit role update or reissue.
- Easy Auth configuration was not changed.

## R5 Remediation

Changed files:

- `backend/invitation_store.py`
- `tests/test_entra_member_invites.py`
- `tests/test_workspace_roles.py`
- `.superpowers/sdd/p2-b-task-4-report.md`

Relevant existing authorization behavior: `backend/workspace_authz.py` uses the journal-derived bootstrap role during activation; the added role tests protect that behavior.

Replay validation now rejects accepted-, failed-, and revoked-first journals; missing accepted identity; mismatched activation identity; invalid or missing role-change fields; duplicate activation; and illegal transition order. Identity comparisons use explicit normalized actor and tenant keys throughout authorization, activation, idempotent acceptance, and alias revocation.

The added adversarial tests cover stale metadata `admin` and `owner` against a journal `viewer`, non-pending first state events, missing/mismatched identities, malformed role changes, reverse JSON key-order alias revocation, and pending retry after a role change.

## TDD And Verification

Red run after adding the r5 replay/retry tests:

```text
7 failed, 67 passed in 6.07s
```

The failures demonstrated that replay accepted non-pending first states, treated `('', '')` as a valid identity key, allowed malformed role changes to escape as a transition error, and duplicated a pending invitation after a journal role change.

Final focused run:

```text
76 passed in 5.21s
```

Final full run:

```text
548 passed, 1 warning in 47.93s
```

The warning is the existing `ExperimentalWarning` from `backend/maf_team_runtime.py:1060`.

Final checks:

```text
python -m compileall -q backend tests    # exit 0
imports ok
git diff --check                         # exit 0
```

## Historical Note

Earlier remediation reports described intermediate behavior and are superseded by this current contract. In particular, no current claim relies on an undocumented Graph `invitedUser.tenantId`, and workspace-member metadata is not an authorization source for invited membership.
