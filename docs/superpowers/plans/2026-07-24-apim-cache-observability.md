# APIM and Cache Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a truthful, compact monitoring experience for APIM-governed inference, Redis cache reuse, model cost, and request provenance.

**Architecture:** Persist safe cache state and source metering alongside model events. Aggregate only eligible cache events in the backend monitoring payload. Render the bounded API response in a compact dashboard with a request drawer; preserve APIM aggregates as an independent evidence source.

**Tech Stack:** FastAPI, Blob-backed run store, Redis, Azure Monitor/APIM metrics, React, Vite, Node test runner, pytest.

## Global Constraints

- Do not change Easy Auth or workspace authorization behavior.
- Do not expose prompts, raw Entra IDs, headers, tokens, connection strings, or cache keys.
- Do not represent model price-card estimates as Azure billing.
- Count only text-model cache events; APIM and Redis are distinct data sources.
- Keep the request list bounded and newest-first.

---

### Task 1: Persist safe cache metering

**Files:**
- Modify: `backend/orchestrator.py:2087-2157`
- Modify: `backend/run_store.py:94-132`
- Test: `tests/test_run_store_dynamic.py`
- Test: `tests/test_orchestrator_smoke.py`

**Interfaces:**
- Produces: a normalized model `cache` record containing `state`, `provider`,
  optional `elapsed_ms`, optional `source_usage`, and optional
  `source_cost_estimate`.
- Consumed by: `backend/monitoring_dashboard.py` aggregation.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_record_event_persists_safe_cache_metering():
    run_store.start_run("cache-run", "ws-a", "Analyze", {})
    run_store.record_event("cache-run", "model_response", {
        "deployment": "gpt-5.6-sol",
        "route": "analysis",
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "cache": {"state": "hit", "provider": "redis", "elapsed_ms": 3,
                  "source_usage": {"prompt": 10, "completion": 2, "total": 12}},
    })
    result = run_store.complete_run("cache-run")
    assert result["models"][0]["cache"]["state"] == "hit"
    assert result["models"][0]["cache"]["source_usage"]["total"] == 12
```

- [ ] **Step 2: Run failing test**

Run: `python -m pytest tests/test_run_store_dynamic.py -q`

Expected: the new assertion fails because `model_record` drops cache metadata.

- [ ] **Step 3: Implement cache payload and source meter**

Store a private cache payload in Redis containing the feasibility result plus a
bounded meter derived from the source model's observed usage and price-card
estimate. On a hit, restore only that safe meter under `_llm.cache`; never
restore prompts, response IDs, or keys. Normalize this data in `record_event`
into the persisted model record.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_run_store_dynamic.py tests/test_orchestrator_smoke.py -q`

Expected: all selected tests pass.

### Task 2: Aggregate cache and bounded requests

**Files:**
- Modify: `backend/monitoring_dashboard.py:20-70, 493-650`
- Test: `tests/test_monitoring_dashboard.py`
- Test: `tests/test_monitoring_dashboard_api.py`

**Interfaces:**
- Consumes: persisted `models[].cache` records.
- Produces: `summary.cache` and at most 30 safe `requests` rows in the
  monitoring payload.

- [ ] **Step 1: Write failing aggregation tests**

```python
def test_dashboard_aggregates_cache_only_for_eligible_model_events():
    dashboard = build_monitor_dashboard(...)
    assert dashboard["summary"]["cache"]["eligible"] == 3
    assert dashboard["summary"]["cache"]["hits"] == 1
    assert dashboard["summary"]["cache"]["hit_rate_pct"] == pytest.approx(33.33)
    assert dashboard["summary"]["cache"]["avoided_tokens"] == 120
    assert dashboard["requests"][0]["cache"]["state"] == "hit"
```

- [ ] **Step 2: Run failing test**

Run: `python -m pytest tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py -q`

Expected: the new keys are absent before implementation.

- [ ] **Step 3: Implement aggregation**

Add a pure cache aggregator that treats `hit`, `miss`, and `unavailable` as
eligible states; excludes `bypassed` and unknown legacy events; computes
avoidance only from valid source meter data; returns a partial/unavailable
cost status when source prices are absent. Add a request projection that strips
message/prompt data and bounds the result to 30 items.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py -q`

Expected: all selected tests pass.

### Task 3: Render compact monitoring UX

**Files:**
- Modify: `web/src/monitorDashboardViewModel.js`
- Modify: `web/src/MonitorPage.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/monitorDashboardViewModel.test.mjs`

**Interfaces:**
- Consumes: `summary.cache` and `requests` from `/api/monitoring`.
- Produces: cache KPI, compact request table, and a side drawer for selected
  request-safe provenance.

- [ ] **Step 1: Write failing view-model tests**

```js
test("monitor view model separates Redis reuse from APIM evidence", () => {
  const view = monitorDashboardViewModel({
    summary: { cache: { eligible: 3, hits: 1, misses: 1, unavailable: 1, hit_rate_pct: 33.33,
                         avoided_tokens: 120, avoided_cost: { status: "estimated", amount: 0.001, currency: "USD" } } },
    gateway: { state: "verified", governed_calls: 7, total_tokens: 420 },
  });
  assert.equal(view.cache.hitRateLabel, "33%");
  assert.equal(view.gateway.callsLabel, "7");
});
```

- [ ] **Step 2: Run failing test**

Run: `node --test web/src/monitorDashboardViewModel.test.mjs`

Expected: the new cache view model field is undefined.

- [ ] **Step 3: Implement stable UI**

Add the cache card inside the existing KPI grid, replace the dense secondary
content with a bounded recent-request table, and open a fixed-width side
drawer only after row selection. Use fixed grid tracks, `min-width: 0`, and
overflow handling so controls do not shift or overflow at desktop and mobile
breakpoints. Use labels that distinguish `APIM 已验证` from `Redis 命中`.

- [ ] **Step 4: Run frontend tests and build**

Run: `node --test web/src/*.test.mjs && npm run build`

Expected: all tests pass and Vite produces a build.

### Task 4: Verify and deploy production

**Files:**
- Modify: `docs/monitoring-azure-state.md`

**Interfaces:**
- Deploys: new backend and frontend container images to `ca-dataforge-backend`
  and `ca-dataforge-web`, with backend before frontend.

- [ ] **Step 1: Run complete automated verification**

Run: `python -m pytest -q`

Expected: zero failures.

- [ ] **Step 2: Build immutable container images**

Run Azure Container Registry builds for backend and frontend with the commit
SHA in each image tag; record image digest and revision names without secrets.

- [ ] **Step 3: Deploy staged revisions**

Deploy backend first with zero traffic, call `/api/health`, then deploy the
frontend with its upstream set to that staged backend. Do not mutate Easy Auth
settings.

- [ ] **Step 4: Production acceptance**

Create a fresh eligible cache miss followed by a matching cache hit from an
owner session. Verify the persisted run records, dashboard API aggregation,
and APIM metric ingestion. Switch production traffic only after backend and
frontend checks pass; retain the prior revisions for rollback.
