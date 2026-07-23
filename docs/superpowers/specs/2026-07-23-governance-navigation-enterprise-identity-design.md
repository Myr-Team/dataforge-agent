# Governance Navigation and Enterprise Identity Design

## Status and objective

**Status:** information architecture approved on 2026-07-23; pending review of
this specification and implementation planning.

**Objective:** turn DataForge from a dense demo-like workspace into an
operational product surface. The left navigation must separate day-to-day data
work from collaboration and governance. Enterprise members must see useful,
verified identities instead of opaque hash labels, without exposing raw Entra
claims or unverified external identities.

This design adopts the approved **B+ governance navigation**: collaboration,
audit, cost/ROI, and model/connections are separate destinations with
role-aware visibility.

## Scope

### In scope

1. A two-group navigation rail with stable sizing and explicit ownership
   boundaries.
2. Separate destinations for member collaboration, audit/lineage, cost/value,
   and model/connections.
3. A display-safe enterprise identity projection for active workspace members.
4. Owner-authoritative access rules for governance surfaces and API data.
5. Migration of existing Settings and Monitor content into the new
   destinations without inventing new data or metrics.
6. Desktop-first visual QA at the user's effective 1536 x 960 browser viewport
   and responsive checks down to 1024 x 800.

### Out of scope

- Changes to Easy Auth, Entra tenant configuration, token storage, or login
  behavior.
- Sending directory invitations through email/Graph beyond the existing
  invitation capability.
- Exposing raw Entra claims, object IDs, access tokens, prompts, transcripts,
  credentials, or unredacted telemetry.
- Hard-coded identity, industry, dataset, score, health, cost, or ROI values.
- Replacing existing analysis, data-workbench, artifact, or run APIs solely to
  support the visual reorganization.

## Product information architecture

The left rail has two groups. The first is the operating workspace. The second
is governance. Settings no longer carries unrelated monitoring, member, or
cost panels.

| Group | Destination | Purpose | Visibility |
| --- | --- | --- | --- |
| Workspace | `工作区` | current project status, evidence summary, active analysis, iteration | authorized workspace user |
| Workspace | `数据` | assets, imports, edits, quality, and field mapping | authorized workspace user |
| Workspace | `运行记录` | run outcome and trace for the current workspace | authorized workspace user, filtered by backend permission |
| Workspace | `会话` | conversation and follow-up work | authorized workspace user |
| Workspace | `产物` | generated and uploaded deliverables | authorized workspace user |
| Governance | `成员与协作` | members, invitations, roles, and personal activity | authorized workspace user; mutations follow role policy |
| Governance | `审计与溯源` | invocation lineage, data/evidence revisions, and audit events | every member sees own activity; Owner sees cross-member lineage |
| Governance | `成本与价值` | model/token attribution, qualified cost, quality evidence, and ROI state | Owner only |
| Governance | `模型与连接` | model routes, text gateway coverage, connectors, and runtime configuration | Owner only |
| Governance | `设置` | workspace naming, lifecycle, and non-governance preferences | Owner only |

`审计与溯源` replaces the generic `监视` label. It is a purpose-specific
lineage view, not a second run-history page. `成本与价值` is a compact BI
dashboard and retains the truthfulness rules of the existing Monitor design.
`模型与连接` is a configuration and coverage view; it does not duplicate the
data page's import workflow.

### First-entry behavior

On first entry, the rail is fixed-width and the selected destination has a
single clear content title. Each governance page begins with one bounded status
summary, followed by progressively disclosed details. It must not preload every
member, trace, token, model, and connector card into Settings.

The active navigation selection, rail width, content width, button positions,
and chart frames remain stable across loading, empty, denied, and ready states.
No navigation action may resize nearby controls or cause the page shell to
shift.

## Enterprise identity and privacy model

### Trusted identity policy

The backend remains the authority for identity visibility. A member receives a
`verified_enterprise` display projection only when all conditions are true:

1. the actor is an active member of the current workspace;
2. Easy Auth supplied a validated email and tenant identity through the existing
   trusted identity path;
3. the email domain matches the workspace's configured enterprise-domain
   allowlist; and
4. the displayed name is a validated identity claim or a safe email-local-part
   fallback.

The enterprise-domain allowlist is an explicit deployment/workspace policy,
not a hard-coded person or a string inferred from data. It is seeded and
maintained by an authorized owner or deployment operator. Domain matching is
case-insensitive and exact on the email suffix. A domain mismatch, missing
email, missing tenant confirmation, external invitation, or unaccepted
invitation results in a pseudonymous display.

The frontend never decides whether an identity is trusted. It renders the
server's `identity_visibility` enum and fields only.

### Public member projection

The existing member contract is extended with an allowlisted display object:

```json
{
  "subject_label": "member_7aa2...",
  "identity_visibility": "verified_enterprise",
  "display": {
    "name": "Example Person",
    "email": "person@enterprise.example"
  },
  "role": "owner",
  "status": "active",
  "source": "entra",
  "usage": { "known_tokens": 1200, "unknown_runs": 1 }
}
```

For `pseudonymous` or `pending_verification`, `display.name` and
`display.email` are omitted. `subject_label` remains available only as a
technical reference in expanded authorized details and is never the primary
customer-facing member name. Raw actor IDs, tenant IDs, claims, email aliases,
and tokens are never returned.

The real-name view is limited to workspace members who are themselves allowed
to read the member directory. An owner may see all trusted members of that
workspace. A member can see the same trusted directory but receives only their
own audit activity; cross-member activity stays owner-only.

### Auditing and attribution

Persisted runs and telemetry continue to use safe actor references. The
`审计与溯源` API resolves a display name only after the requesting actor passes
the workspace and visibility policy. It emits a bounded row with:

- display-safe initiator name or pseudonym;
- route type, model/deployment identifier, duration, token coverage, and
  sanitized correlation ID;
- workspace/data/evidence revision identifiers;
- audit status and allowed reason code; and
- timestamp and source freshness.

It never returns raw prompt text, model reasoning, chain-of-thought, or raw
Foundry/APIM payloads.

## Destination responsibilities and contracts

### Members and collaboration

The collaboration page contains three sections: active members, pending
invitations, and role-aware activity. It uses the existing member APIs and a
server-projected member directory. Owners can invite, revoke, and alter roles
only when the authoritative backend capability says they can. Failed reads are
shown as `denied` or `unavailable`, never as a successful empty list.

### Audit and lineage

This page assembles a compact, paginated read model from run summaries,
allowlisted audit events, and trace delivery status. The row title is a
human-readable action and initiator; the technical correlation ID belongs in
details. A member query is constrained to that member's own activity. An Owner
can filter the workspace by actor, route, time, status, audit outcome, and data
revision.

### Cost and value

This reuses the Monitor BI read model but appears as its own navigation
destination. It shows observed/unknown token counts, qualified model-cost
estimates, model/route distribution, quality/audit coverage, and ROI in one of
three truthful states: `verified`, `pending_verification`, or `unavailable`.
The page is Owner-only in both navigation and backend authorization. Missing
price, usage, outcome, or evaluator evidence remains visibly unknown rather
than being represented as zero or a success.

### Models and connections

This page combines two display-safe sources: text-model route coverage and
workspace connection state. It shows the selected deployment/model, route
coverage, latency/usage freshness, and connector health/status. Secrets,
connection strings, credentials, and raw tool responses stay server-side. A
connector with no fresh observation is `unavailable` or `not recorded`, never
implicitly healthy.

### Settings

Settings is reduced to workspace lifecycle and user-facing preferences. It
links to the dedicated governance destinations when a related management task
is needed, rather than rendering their complete content inline.

## Frontend structure

The shell navigation uses one declarative item schema with these fields:
`id`, `label`, `icon`, `group`, `required_capability`, `route`, and optional
`badge_state`. Rendering comes from the authoritative workspace capability
response rather than local assumptions about the user role.

The governance pages share a fixed page frame and a standardized state model:
`loading`, `ready`, `empty`, `denied`, `unavailable`, and `partial`. Each page
has a stable top summary, one primary data region, and links to deeper details.
No page uses nested decorative cards or a hard-coded overview result.

For the effective 1536 x 960 desktop viewport, the rail remains between 224
and 240 CSS pixels and the content area uses an explicit `minmax` grid. At
1024 x 800, the rail remains readable and dense tables horizontally scroll
inside their own region instead of resizing the whole app or clipping buttons.

## Backend authorization and compatibility

Every new page reads through a dedicated backend capability check. Hiding a
navigation item is not authorization. Direct requests to Owner-only cost,
model, connection, or cross-member lineage endpoints must return a bounded 403
reason code to unauthorized actors.

Existing Monitor and Settings endpoints stay compatible during migration. New
page-specific projections may be added where client-side joins would otherwise
mix privacy scopes. Each projection returns its own freshness/source status so
that one unavailable upstream source cannot erase other recorded data.

## Acceptance criteria

1. An active member with an email in the configured enterprise domain sees
   their verified name and email in the member directory; an external or
   unverified member sees neither field.
2. No response exposes raw Entra IDs, tenant IDs, claims, tokens, credentials,
   prompts, or unredacted telemetry.
3. An Owner sees all B+ governance destinations. A non-owner cannot fetch
   Owner-only cost/value, model/connection, or cross-member lineage data by
   direct API request.
4. A non-owner can see their own run/audit activity with a display-safe
   identity, but cannot filter or discover another member's activity.
5. Each navigation destination has distinct data responsibilities; Settings
   does not again become a container for members, lineage, BI, and connectors.
6. Loading, empty, denied, unavailable, and partial states occupy stable page
   frames without shell or button movement at 1536 x 960 and 1024 x 800.
7. Monitor-derived metrics remain source-backed and explicitly qualified;
   unavailable cost or ROI is never rendered as a numeric value or healthy
   status.
8. Backend unit/API tests, frontend view-model tests, frontend build, signed-in
   candidate smoke tests, and browser checks of every navigation destination
   pass before any production traffic change.

## Delivery sequence

1. Add the trusted enterprise-domain policy and display-safe member projection,
   with backend tests for trusted, external, missing-email, and unauthorized
   cases.
2. Add backend capability/read-model boundaries for own versus cross-member
   lineage and Owner-only governance views.
3. Refactor navigation and split the existing Settings/Monitor content into
   the B+ destinations, preserving stable dimensions and truthful states.
4. Add integration tests for role-gated direct API access and frontend tests
   for capability-driven navigation.
5. Build zero-traffic Container Apps candidate revisions and run signed-in
   desktop/mobile smoke tests. Promote only after explicit user approval.
