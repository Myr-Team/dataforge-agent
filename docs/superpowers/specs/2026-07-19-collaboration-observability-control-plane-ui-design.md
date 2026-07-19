# Collaboration and Observability Control Plane UI

## Status and scope

**Status:** proposed design, approved in principle on 2026-07-19; implementation
starts only after this document is reviewed and the existing conversation
metadata security work is complete.

**Scope:** make DataForge usable as a shared workspace by improving member
invitation, permission visibility, run observability, and work-area feedback.
The target user for the first release is a workspace owner or administrator.
Editors retain execution and contribution views; viewers retain read-only views.

**Out of scope:** changing Easy Auth, implementing a new identity provider,
building a separate enterprise administration portal, or exposing raw prompts,
claims, tokens, credentials, or full telemetry payloads in the product UI.

## Production UI audit (2026-07-19)

The audit used a signed-in production workspace, `选址情报演示（模拟）`.

### P0 usability findings

1. **Data workbench renders the generic error boundary.** Opening `数据`
   displayed `这个页面出了点问题`; the user can only refresh or switch views.
   The current boundary protects against a whole-page crash but discards the
   actual failure context, making recovery and diagnosis poor.
2. **Member and permission loading fails for the workspace owner.** `成员与权限`
   showed `成员与操作权限读取失败，请重试`; retry did not recover. The member
   count was zero, usage was unavailable, and directory search, invite inputs,
   role selection, and the invite action were all disabled. This blocks the
   feature being introduced.

### Information and interaction findings

1. The run page has truthful underlying values for duration, token usage, tools,
   audit state, trace ID, and model, but presents too many equal-weight cards
   before the user can understand the outcome and who initiated it.
2. A lightweight follow-up can display a detailed multi-agent visual trace,
   while also reporting one agent and no tool calls. The screen needs a clear
   run-type badge and should collapse inapplicable sections rather than make a
   lightweight run look incomplete.
3. Run history searches title, ID, status, and verdict only. It cannot filter by
   actor, run type, time, outcome, or audit status, and it does not make the
   associated data version or resulting artifact visible at a glance.
4. Settings mixes service health, environment configuration, workspace members,
   and governance into one dense page. `全部正常` should only appear when every
   dependency has a fresh recorded result; an unrecorded storage capacity must
   remain `未记录`, not be visually overwhelmed by a global healthy label.
5. The shell mixes Chinese with `Invite members`, `Workspace`, and `Role`.
   Customer-facing workspace navigation should be consistently localized.
6. The workspace home explains the active analysis well, but does not show
   collaboration signals: pending invitations, recent member activity, or an
   active run owner and progress summary.

## Chosen information architecture

Keep the existing `设置` and `运行记录` navigation entries. Do not add a new
top-level governance product area in this release.

### Settings: Members and permissions

Use three compact sections within the existing tab:

1. **Active members:** name, role, last relevant activity, and contextual role
   action menu for owners/admins.
2. **Pending invitations:** recipient display label, chosen role, inviter,
   sent/expiry time, delivery state, and actions to resend or revoke.
3. **Access model:** a short, static explanation of Owner/Admin, Editor, and
   Viewer capabilities, followed by an activity link for governance users.

The invite panel begins with the directory search and a selected-person chip.
Manual email entry remains a controlled fallback. It is unavailable only when
the authoritative member read has failed; the screen must show a bounded
failure reason and a working retry, never silently show `0` members as if that
were a successful response.

### Runs: outcome first, trace on demand

The page becomes a two-level view:

1. **Run header:** title, initiator, route type, started/finished time,
   conclusion, data/evidence revision, audit status, and linked artifact.
2. **Expandable execution groups:** routing, evidence retrieval, analysis,
   audit, and production. A lightweight follow-up only shows the groups that
   actually ran; an explanatory badge says that a full audit was not required.

History gets a filter bar for `member`, `type`, `status`, `audit`, and `time`.
Cards show the summary and an accessible short title first; technical IDs and
source paths live in the expanded details. Token usage remains a numeric value
with its source and model, not an estimated amount.

### Workspace home: collaboration feedback

Add a narrow, data-backed strip below the existing summary area:

- active analysis: route, owner, elapsed state, and link to the run;
- recent collaboration: invitation accepted/revoked, member joined, outcome
  recorded, or artifact produced;
- pending work: invitations awaiting acceptance and outcomes awaiting review.

The strip is hidden rather than replaced with demo copy when no authoritative
data is available.

## Data contracts and privacy boundary

Existing API contracts are the starting point:

- `GET /api/workspaces/{id}/members`
- `GET /api/workspaces/{id}/members/entra-users`
- `POST /api/workspaces/{id}/members/invite`
- `POST /api/workspaces/{id}/members/entra-invite`
- `PATCH` and `DELETE /api/workspaces/{id}/members/{subject_ref}`
- `GET /api/workspaces/{id}/usage-summary`
- `GET /api/workspaces/{id}/governance/audit-events`
- `GET /api/runs/{id}/summary`, `/trace`, `/pipeline`, and `/structured-result`

Before the UI consumes these contracts, the conversation and trace work must
emit only typed, allowlisted metadata for persisted and streamed records.
The UI may receive a display-safe actor label, route enum, evidence revision,
usage totals, bounded status, and bounded reason code. It must not receive raw
prompt/history text, Entra claims, email addresses outside an authorized member
management view, credentials, model rationale, raw clarification options, or
raw Composer errors.

If the current endpoints require multiple dependent calls to assemble a single
view, add a bounded workspace control-plane summary endpoint rather than
duplicating client-side joins. It must return explicit load states per section
so that a failed member query does not masquerade as an empty member list.

## Error handling and empty states

- Keep the error boundary, but emit a safe correlation ID to the UI and record
  an allowlisted diagnostic event. It must not show a stack trace or raw API
  payload.
- Every data panel distinguishes `loading`, `empty`, `denied`, `unavailable`,
  and `ready` states.
- Retry actions must reissue the relevant request and visibly transition through
  loading. A second failure displays a stable reason code plus a non-destructive
  recovery action.
- Health chips show a freshness timestamp or `未记录`; they must never infer a
  healthy aggregate from missing dependency results.

## Acceptance criteria

1. A signed-in owner can load active members, pending invitations, member usage,
   and governance activity without a disabled invite panel.
2. Inviting, revoking, and changing a role update the visible state without
   showing raw Entra tokens or claims.
3. An editor or viewer sees only the controls and activity permitted by the
   authoritative backend response.
4. A full analysis and a lightweight follow-up show different, truthful run
   structures; neither invents an audit, agent, tool, duration, or token value.
5. Run history filters operate on API-backed metadata, not fixed demo labels.
6. The data workbench renders successfully with real workspace data, and any
   recoverable failure has a bounded diagnostic path.
7. Desktop and mobile browser checks cover home, data, runs, conversations,
   artifacts, settings, members, and governance.
8. Backend tests, frontend tests/build, signed-in candidate checks, and a
   production promotion review all pass before a traffic change.

## Delivery sequence

1. Finish strict allowlist projection for persisted conversation metadata and
   every SSE event; verify no raw sensitive context can reach a UI surface.
2. Diagnose and repair the two production P0s: data-workbench rendering and
   owner member-read recovery.
3. Implement the members-and-invitations control surface using authoritative
   endpoint states.
4. Restructure runs around outcome-first summary, filtered history, and grouped
   trace details.
5. Add workspace collaboration feedback.
6. Build zero-traffic candidate revisions, run signed-in desktop/mobile checks,
   and request explicit approval before production traffic changes.
