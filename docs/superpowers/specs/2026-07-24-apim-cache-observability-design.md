# APIM and Cache Observability Design

## Goal

Make DataForge's production monitoring page a concise, truthful operational
view of governed text inference, Redis cache reuse, model consumption, and
request-level provenance.

## Constraints

- Only persisted run telemetry and Azure/APIM aggregate metrics may be shown
  as observed values.
- APIM governs text inference only. Image, speech, search, Blob, and SQL
  operations must not be counted as APIM-governed text calls.
- Cache reuse is application-level Redis reuse, not an APIM metric. It must
  have separate labels and aggregation.
- Cost avoidance may only be shown when the source model call had persisted
  token usage and a persisted estimated price. Older cache entries without
  source metering remain explicitly unpriced.
- Member labels remain backend-authorized public projections; raw Entra object
  identifiers and prompts are not exposed.
- Authentication behavior is unchanged.

## Data Contract

Each persisted model event will include a safe `cache` object when applicable:

```json
{
  "state": "hit|miss|unavailable|bypassed",
  "provider": "redis",
  "elapsed_ms": 4,
  "source_usage": {"prompt": 120, "completion": 40, "total": 160},
  "source_cost_estimate": {"status": "estimated", "amount": 0.0012, "currency": "USD"}
}
```

`source_usage` and `source_cost_estimate` only appear on cache hits whose
stored result originated from a metered model call. They are used for avoided
usage/cost estimates and never replace actual observed Token consumption.

The `GET /api/monitoring` response gains:

```json
{
  "summary": {
    "cache": {
      "eligible": 12,
      "hits": 4,
      "misses": 7,
      "unavailable": 1,
      "hit_rate_pct": 33.33,
      "avoided_tokens": 640,
      "avoided_cost": {"status": "estimated", "amount": 0.0048, "currency": "USD", "unpriced_hits": 0}
    }
  },
  "requests": [
    {
      "run_id": "...",
      "occurred_at": "...",
      "member_label": "...",
      "workspace_label": "...",
      "route": "analysis",
      "deployment": "gpt-5.6-sol",
      "status": "completed",
      "duration_ms": 1280,
      "tokens": {"total": 160},
      "cache": {"state": "hit", "provider": "redis", "elapsed_ms": 4},
      "trace": {"trace_id": "...", "agent_id": "dataforge-runtime-v1"}
    }
  ]
}
```

The request list is bounded, ordered newest first, has no prompt/message/body,
and contains only fields already safe for the owner to view.

## Page Structure

The independent `Monitor` navigation item uses a compact operational layout:

1. Four stable KPI cards: APIM-governed calls, observed Tokens, estimated model
   cost, and Redis cache reuse.
2. Two primary charts: daily call/Token trend and model consumption/cost.
3. Three concise panels: APIM proof, model route distribution, and member
   attribution.
4. A bounded recent-request table. Selecting a row opens a side drawer with
   request-safe provenance, cache state, Token data, duration, route,
   associated run, and Foundry trace reference.

The page never derives a billing figure from the browser. Actual APIM metric
evidence and per-run telemetry remain visibly separate.

## Error and Freshness Behavior

- Redis unavailable is recorded as `unavailable`, not silently called a miss.
- APIM metric ingestion can be delayed; the UI reports the most recent
  observation time and its verification state.
- Old runs without cache telemetry remain outside the cache denominator.
- An empty request list is an explicit empty state, not synthetic demo rows.

## Acceptance Evidence

1. A cache miss persists safe source metering; a following hit persists cache
   state and supports hit-rate/avoidance aggregation.
2. A Redis outage appears as `unavailable` and does not inflate hit rate.
3. Monitoring API retains APIM aggregate evidence separately from run and
   Redis metrics.
4. Frontend tests prove labels distinguish observed Tokens, estimated cost,
   APIM evidence, and cache reuse.
5. Production verification performs a fresh text request, verifies its run
   record, checks APIM metric evidence after ingestion, and captures the
   monitoring UI from an authenticated owner session.
