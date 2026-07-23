# Model Routing and Cost Control Design

## Status and objective

**Status:** approved for implementation on 2026-07-23.

**Objective:** make DataForge's model governance demonstrable and operational:
an Owner can map real, allowlisted Foundry deployments to execution kinds,
choose a one-off route for an eligible request, and inspect measured token use
and price-card-based estimated cost for each run. The monitoring experience
must make model allocation, consumption, attribution, and value evidence easy
to understand without claiming an Azure invoice or a verified ROI where the
evidence is absent.

## Product boundaries

### In scope

1. Replace the passive `models-connections` destination with an Owner-only
   `model-routing` destination.
2. Expose only server allowlisted Foundry text deployments as selectable
   routes. The initial deployment inventory is configuration, not a frontend
   hard-coded model list.
3. Persist an Owner-managed, workspace-specific execution policy with primary
   and fallback route IDs for `direct_reply`, `follow_up`, `full_analysis`,
   and `audit_repair`.
4. Accept an Owner's request-level `model_route_id` override for a new chat or
   analysis request. It is validated against the same workspace policy and
   server allowlist, and it is never a raw deployment name.
5. Persist safe route-selection metadata with every model response: route ID,
   deployment, execution kind, selection source, fallback reason, workspace
   policy revision, and price-card revision.
6. Add a workspace price card. Every row has route ID, input/output price per
   million tokens, currency, source label, and an updated timestamp. Owner is
   the only editor and reader of price values.
7. Calculate estimated cost from observed provider token counts and the exact
   price-card revision selected at run start. Missing token usage or a missing
   price is `unavailable`, never zero.
8. Make the Owner-only monitoring destination a clear dashboard with
   measured run/token figures, estimated cost, breakdowns by route/execution
   kind/member, and a visible separation between estimated savings and
   verified ROI evidence.
9. Remove the thin left-nav Settings page. Account identity and theme controls
   live in the avatar menu; workspace governance configuration lives next to
   the governed feature that owns it.

### Out of scope

- Easy Auth, Entra sign-in, token storage, tenant configuration, and Key Vault
  configuration changes.
- Changes to APIM authentication or its policy until the candidate endpoint
  proves that each configured model deployment can be invoked through the
  current gateway.
- Automatic discovery of Azure invoice prices or an assertion that estimated
  cost equals an invoice amount.
- Fabricating ROI, savings, price values, model availability, or workload
  classifications from dataset names or keywords.
- A fictional "batch" execution category. It is introduced only if a real
  batch request path is implemented.

## Routing model

### Server-owned allowlist

`DF_MODEL_ROUTE_ALLOWLIST` remains the server-owned source of available
routes. Each route contains a stable route ID, deployed Foundry deployment
name, display label, and supported capabilities. A workspace policy can only
reference route IDs currently returned by this allowlist.

This preserves the boundary between a customer-facing selection and the
actual deployment identifier. The client cannot submit a provider endpoint,
deployment, API key, APIM URL, or a custom model name.

### Workspace policy contract

The workspace metadata receives a versioned `model_routing_policy` object:

```json
{
  "revision": 3,
  "updated_at": "2026-07-23T09:30:00Z",
  "updated_by": { "subject_label": "member_ab12" },
  "assignments": {
    "direct_reply": { "primary_route_id": "luna", "fallback_route_id": "terra" },
    "follow_up": { "primary_route_id": "terra", "fallback_route_id": "luna" },
    "full_analysis": { "primary_route_id": "sol", "fallback_route_id": "terra" },
    "audit_repair": { "primary_route_id": "sol", "fallback_route_id": "terra" }
  }
}
```

The sample IDs show the shape only; real routes are supplied by the backend.
Every primary and fallback must support the capability required by its
execution kind. Invalid or stale entries produce a bounded configuration error
and fall back only to the server's capability-compatible default. The fallback
is recorded with a safe reason code.

### Request-level override

`ChatRequest` gains the optional `model_route_id`. The backend honors it only
for a workspace Owner, only for an allowlisted route that supports the target
execution kind, and only for the request being initiated. The persisted
selection is `manual`; a rejected override returns a 422 reason code rather
than silently changing the selected deployment.

Automated analysis remains separate from the conversation transcript: it uses
its origin/execution kind and any explicit allowed override, but does not
pretend an automated run is a user chat message.

## Cost and value evidence

### Price-card contract

```json
{
  "revision": 5,
  "currency": "USD",
  "entries": [
    {
      "route_id": "terra",
      "input_per_million": 0.0,
      "output_per_million": 0.0,
      "source_label": "Owner-maintained pricing reference",
      "updated_at": "2026-07-23T09:30:00Z"
    }
  ]
}
```

Entries must be finite, non-negative numbers and may not contain secrets or
connection information. A blank card is valid and yields `unavailable` cost.
The UI labels all price-card calculations as `estimated`. It never renders an
unconfigured rate as a zero-cost result.

### Per-model response evidence

The Foundry response already records provider usage. The response metadata is
extended with a `cost_estimate` object only when both observed input/output
tokens and a pinned matching price-card entry exist:

```json
{
  "status": "estimated",
  "currency": "USD",
  "amount": 0.001234,
  "formula": "input_tokens/1_000_000*input_per_million + output_tokens/1_000_000*output_per_million",
  "price_card_revision": 5,
  "route_id": "terra"
}
```

Without complete evidence it instead returns a safe unavailable state with a
reason such as `usage_not_recorded` or `price_not_configured`. It returns no
price values to non-Owner audiences. Runs aggregate only their own persisted
model-response records, deduplicated by response/usage event ID.

### Monitoring dashboard

The Owner-facing dashboard has a stable header summary and four bounded data
regions:

1. **Observed consumption:** calls, input, output, total tokens, and unknown
   token coverage.
2. **Estimated cost:** current-window estimated spend and unpriced calls,
   sourced from the workspace price-card revision.
3. **Model allocation:** rows and compact bars by route/deployment and
   execution kind, with selected/manual/fallback counts.
4. **Attribution and value evidence:** member attribution and a strict status
   distinction between `verified`, `pending_verification`, and
   `unavailable` ROI/outcome evidence.

The dashboard can compare two configured routes only as a labelled simulation
using the same observed token composition and the selected price-card entries.
It calls the result `estimated alternative cost`; it never calls it savings or
ROI until a qualified baseline and outcome evidence exist.

## API contracts

All endpoints require an authorized workspace actor. Policy and price-card
mutations, raw price-card entries, and the dashboard are Owner-only.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/workspaces/{id}/governance/model-routing` | allowlisted routes, effective workspace policy, and safe recent allocation summary |
| `PUT` | `/api/workspaces/{id}/governance/model-routing` | validate and persist a full policy revision |
| `GET` | `/api/workspaces/{id}/governance/model-price-card` | Owner price-card editor/read model |
| `PUT` | `/api/workspaces/{id}/governance/model-price-card` | validate and persist the next price-card revision |
| `GET` | `/api/workspaces/{id}/governance/monitor-dashboard` | observed token/cost/attribution/ROI dashboard using persisted evidence |

`GET model-routing` is an Owner read because it reveals the workspace's model
allocation. The existing public monitoring snapshot remains compatibility-only
and cannot expose editable price values.

## Interface and visual design

The governance rail contains, in order: `成员与协作`, `审计与溯源`, `监视`, and
`模型路由`. `设置` disappears from the rail. The avatar menu provides account
identity, theme (`system`, `light`, `dark`), and sign-out. Its dimensions are
fixed so opening it cannot shift nearby buttons.

The model routing page uses one page-level title and a dense, fixed-grid model
matrix rather than a stack of nested cards. A single `配置路由` command opens a
DataWorkbench-style modal. The modal uses rows for execution kind, primary
route, and fallback route. A separate `价格卡` command opens a constrained
price editor. Loading, empty, denied, unavailable, and ready states reserve
the same frame dimensions at the effective 1536 x 960 desktop viewport and at
1024 x 800.

## Authorization, privacy, and audit

All policy edits use the existing workspace owner authorization and an audit
event with action name, revision, and safe route IDs only. Price-card source
labels must not contain secrets. Raw Entra identifiers, prompts, provider
payloads, API keys, deployment endpoints, credentials, or cost data for
unauthorized users are never returned.

## Acceptance criteria

1. The owner sees only routes returned by the server allowlist and can map an
   eligible primary/fallback route to each supported execution kind.
2. An invalid, stale, or capability-incompatible route selection is rejected;
   no raw deployment can be injected by a request.
3. A manual Owner selection is persisted as `manual` in a real run's model
   metadata; a normal run is persisted as `workspace_policy` or `default`.
4. A real model response with recorded input/output tokens and a matching
   price card exposes a per-response estimated cost and a pinned price-card
   revision. Missing price/usage is visibly unavailable, not zero.
5. The dashboard breakdowns reconcile to persisted run response records by
   model, execution kind, and member, and no non-Owner can retrieve cost or
   policy data through a direct API call.
6. Settings is no longer a left-nav destination; profile/theme controls work
   from the avatar menu without a page-shell layout shift.
7. Backend unit/API tests, frontend view-model tests, frontend build, a
   zero-traffic candidate deployment, candidate API smoke checks, and signed-
   in browser validation pass before production traffic changes.

## Delivery sequence

1. Add the isolated backend policy/price-card domain functions and failing
   tests.
2. Wire selected policy/override context into chat and analysis execution,
   then persist per-response cost evidence.
3. Add owner-gated control-plane endpoints and monitoring aggregation.
4. Replace the rail destination and add the routing/price-card UI and avatar
   preferences.
5. Run unit, API, and browser verification; deploy a zero-traffic candidate
   with the real Foundry deployment allowlist; make one manual-route call and
   validate its trace/token/cost evidence before proposing production rollout.
