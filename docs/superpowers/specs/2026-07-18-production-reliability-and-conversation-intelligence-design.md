# Production Reliability and Conversation Intelligence Design

## Context

DataForge is now serving the `convsep` revision in production. Autonomous
analysis has a run identity that is separate from a human conversation, but
two customer-facing concerns remain coupled to trust in the product:

1. A signed-in owner can see `workspace permission denied for workspace.read`
   for an existing workspace.
2. Conversation quality still depends too heavily on path-specific helpers,
   which can make simple replies feel mechanical or cause an unnecessarily
   heavy analysis run.

This design treats access correctness as a release gate and improves the
conversation decision boundary without weakening authorization or hard-coding
business conclusions.

## Goals

- Restore access only when the active Entra identity provably matches an
  existing workspace owner or active member.
- Make a denied request explainable through a bounded, non-sensitive reason
  code and a durable audit event.
- Route each human message to the smallest truthful response mode:
  direct answer, evidence-grounded follow-up, plan draft, complete analysis,
  or clarification.
- Make every response mode render through the same Markdown answer contract.
- Release only after a signed-in production journey proves access, analysis,
  conversation, and artifact behavior.

## Non-goals

- Do not disable RBAC, add a permissive fallback, or grant access by email
  guesswork.
- Do not change Easy Auth, tenant configuration, or client redirect settings.
- Do not hard-code a business domain, a score, an opportunity, or a
  clarification question.
- Do not make a new full analysis run for ordinary follow-up chat.

## Access Reliability

### Authorization model

The backend remains authoritative. It compares the active identity's stable
`actor_id` (Entra object ID) and `tenant_id` against the workspace owner and
active member records. Email is display-only and must not be used to grant
access.

The resolver returns an internal `WorkspaceAccessDecision`:

```text
allowed: bool
role: owner | admin | editor | viewer | null
reason_code: owner_match | member_match | identity_missing |
             tenant_mismatch | membership_missing | role_denied
```

Only the bounded `reason_code` and display-safe role are exposed to the
client. The audit log receives the pseudonymous actor correlation already used
by the application; no raw token, email fallback, or secret is logged.

### Legacy owner repair

For an existing workspace whose metadata lacks an explicit owner member,
authorization may resolve `owner` only when all configured owner claims match
the active trusted identity: object ID and tenant ID. The repair is recorded
as an auditable metadata normalization, not as a broad bypass. A mismatch
continues to deny access.

### Client behavior

The client consumes a structured authorization error instead of showing the
raw backend exception. It distinguishes:

- current workspace unavailable: offer workspace selection and reload;
- signed-in identity not mapped: show an access request or invite path;
- identity could not be forwarded: show a retry state without claiming the
  user lacks permission.

The same error contract is used by workspace dashboard, data workbench,
conversations, runs, and artifacts so one page cannot silently succeed while
another uses a different authorization interpretation.

## Conversation Intelligence

### Routing contract

The coordinator first produces a typed `ConversationRoute`, informed by the
user message, compact conversation history, latest canonical analysis, and
the current evidence revision.

```text
mode: direct | grounded_followup | plan_draft | reanalyze | clarify
reason: bounded explanation for trace and UI
evidence_required: bool
missing_information: string[]
```

The coordinator applies policy after model classification. It may promote a
route to `clarify` only when missing information materially changes a requested
decision, or to `reanalyze` when the user explicitly asks for it or provides
new data. It never downgrades a policy-required clarification into a confident
answer.

### Response modes

- `direct`: natural model response for greetings, explanations, and low-risk
  requests that do not assert workspace facts.
- `grounded_followup`: retrieves current workspace evidence, returns a clear
  conclusion with cited sources and known gaps, without the complete agent
  team.
- `plan_draft`: turns established analysis and user feedback into a structured
  plan, with a producer offer linked to the canonical source run.
- `reanalyze`: creates a new autonomous analysis run only for an explicit
  rerun or changed evidence.
- `clarify`: asks one focused, context-derived question and states why the
  missing input matters.

Every mode returns the same Markdown structure where applicable: conclusion,
evidence or assumptions, risks or gaps, and next action. Sections that have no
truthful content are omitted instead of filled with boilerplate.

### Trace and UI contract

Every human chat run records its route, route reason, evidence revision, and
elapsed time. The run page labels the route as direct reply, evidence follow-up,
plan draft, complete analysis, or clarification. Automatic analysis retains
its separate run identity and is never listed as a human conversation.

## Validation and Rollout

### Automated coverage

- Owner match, member match, identity missing, tenant mismatch, and role denied
  authorization tests.
- Legacy workspace owner normalization test with no email-based grant.
- Contract tests for every route, including proof that simple messages do not
  call the full analysis path and that changed evidence does not reuse stale
  citations.
- Markdown response tests for direct, grounded, plan, and clarification modes.
- Existing run/conversation separation and artifact source-run tests remain
  mandatory.

### Production acceptance

The release gate is one signed-in owner journey on a candidate revision:

1. Load an existing workspace without a permission banner.
2. Open data, runs, conversations, and artifacts using the same identity.
3. Trigger automatic analysis and verify it appears only as a run.
4. Send one simple message, one evidence question, and one plan request;
   verify their routes and rendered Markdown.
5. Generate an artifact from the resulting plan and verify its canonical run
   linkage.

Candidate verification precedes any production traffic change. Production is
then checked through the root health endpoint, revision traffic state, and the
same signed-in journey.

## Failure Handling

- Authorization ambiguity fails closed with an auditable reason code.
- Route-classifier or retrieval failure falls back to a transparent response
  that does not invent workspace facts or erase a user message.
- Streamed content remains visible when a later stage fails; no client-side
  clearing of already delivered text.
- Any production journey failure stops traffic promotion and leaves the last
  healthy revision available for rollback.
