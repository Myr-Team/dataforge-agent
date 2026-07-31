# Operations ROI And Risk Decision Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing ROI and risk tabs into evidence-safe operating decision pages, add structured remediation drafts, and make the Portal cache-first so repeated navigation does not block on redundant requests.

**Architecture:** Add two server-owned read models (`roi/decision` and `risk/decision`) that compose existing FinOps facts without asking the browser to reconcile multiple APIs. Add a separate remediation-draft domain that can promote only allowlisted typed changes into the existing governance action service. Replace the one-off bootstrap cache with a page-scoped stale-while-revalidate data store and pair it with Redis fresh/stale envelopes plus workspace/domain cache revisions.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, Azure SQL via pyodbc-compatible repositories, Redis, React, Vite, Node test runner, Playwright.

## Global Constraints

- Keep Easy Auth, tenant derivation, workspace authorization, MAF, analysis flow, workspaces, sessions, runs, and artifacts unchanged.
- Do not connect Azure Cost Management or represent estimated model cost as an Azure bill.
- Use customer-facing labels such as “统一入口”; do not expose cloud gateway product names in the decision pages.
- Keep scenario, observed, partial, unavailable, and verified states distinct; unknown values never become zero.
- Agent output may explain evidence but may not calculate financial values, approve, promote, or execute a remediation.
- Do not accept arbitrary scripts, policy XML, URLs, resource IDs, cache keys, secrets, prompts, provider responses, raw identities, or internal error bodies.
- Default automatic refresh is 10 minutes and runs only for the visible current tab.
- Browser entries are fresh for 5 minutes, stale-usable through 30 minutes, then expired.
- Do not persist Portal query payloads in `localStorage`; cross-device reuse comes from Redis and SQL.
- `DF_FINOPS_ACTIONS_ENABLED` remains `0`; saving a remediation draft must never execute a production change.
- SQL changes are additive and idempotent.
- Preserve the existing untracked workspaces, browser output, test output, and `.superpowers/brainstorm/` artifacts; never stage them.
- No production deployment or traffic switch is authorized by this plan.

---

## File Structure

### Backend decision read models

- Create `backend/finops/decision_models.py`: typed ROI/risk decision response models and evidence-state enums.
- Create `backend/finops/decision_service.py`: deterministic composition of existing ROI, cost, trend, anomaly, opportunity, and insight evidence.
- Modify `backend/finops/opportunities.py`: remove fixed-percentage monetary savings and accept explicit evidence-backed impact estimates.
- Modify `backend/finops/query.py`: expose a unit-economics trend projection that can consume persisted rollups.
- Modify `backend/finops/query_cache.py`: cache the unit-economics aggregate through the existing query-cache boundary.
- Modify `backend/finops/sql_rollups.py`: add tenant/workspace/window-scoped rollup reads.
- Modify `backend/control_plane.py`: add day-bucketed artifact output counts to the existing ROI cost-value snapshot.
- Modify `backend/finops/router.py`: expose one authorized read endpoint per decision page.
- Create `tests/test_finops_decision_service.py`: pure builder tests.
- Create `tests/test_finops_decision_api.py`: authorization, envelope, and one-request API tests.
- Modify `tests/test_finops_rollups.py`: rollup read scope and fallback tests.
- Modify `tests/test_cost_value_api.py`: artifact output trend tests.

### Remediation drafts

- Create `backend/finops/remediation.py`: models, allowed templates, in-memory repository, service, revision checks, and typed-action promotion.
- Create `backend/finops/sql_remediation.py`: tenant-scoped Azure SQL repository.
- Modify `backend/sql/finops_schema.sql`: additive draft and transition tables.
- Modify `backend/finops/router.py`: draft list/create/read/review/promote/close routes.
- Create `tests/test_finops_remediation.py`: service and safety tests.
- Create `tests/test_finops_remediation_sql.py`: SQL repository tests.
- Modify `tests/test_finops_sql_migration.py`: idempotent migration assertions.
- Create `tests/test_finops_remediation_api.py`: route authorization and conflict tests.

### Server query cache

- Modify `backend/cache_store.py`: atomic namespace revision and bounded Redis lock helpers.
- Create `backend/finops/cache_namespace.py`: workspace/domain version keys and invalidation service.
- Modify `backend/finops/query.py`: add a non-identity permission-scope digest to cacheable queries.
- Modify `backend/finops/query_cache.py`: 5-minute fresh / 30-minute stale envelope and forced revalidation.
- Modify `backend/finops/router.py`: read `refresh=1`, attach cache namespaces, and invalidate only affected domains after writes.
- Modify `tests/test_finops_query_cache.py`: fresh, stale, expired, revalidation, single-flight, and isolation tests.
- Create `tests/test_finops_cache_namespace.py`: domain revision tests.

### Frontend data and presentation

- Create `web/src/finopsDataStore.js`: page-scoped in-memory snapshots, in-flight deduplication, abort handling, prefetch, and invalidation.
- Create `web/src/finopsDataStore.test.mjs`: exact cache lifecycle tests.
- Modify `web/src/api.js`: ROI/risk decision and remediation-draft API functions with optional revalidation.
- Modify `web/src/finopsPreload.js`: compatibility wrapper around the new data store.
- Create `web/src/finopsDecisionViewModel.js`: honest ROI, risk, chart, and remediation projections.
- Create `web/src/finopsDecisionViewModel.test.mjs`: null, partial, verified, and chart-scale tests.
- Create `web/src/finops/DecisionCharts.jsx`: accessible value bridge, evidence maturity, risk matrix, and opportunity portfolio components.
- Create `web/src/finops/FinOpsCapabilityNote.jsx`: shared platform-confirmed/business-verification/governance-boundary explanation.
- Create `web/src/finops/RoiDecisionPage.jsx`: ROI decision layout.
- Create `web/src/finops/RiskDecisionPage.jsx`: risk decision layout.
- Create `web/src/finops/RemediationDraftPanel.jsx`: evidence-backed draft viewer and create/review/promote controls.
- Modify `web/src/FinOpsPortal.jsx`: integrate the new pages and cache-first lifecycle; remove the old ROI/risk page bodies.
- Modify `web/src/styles.css`: responsive, tooltip, focus, reduced-motion, and non-shifting update styles.

### Browser acceptance and documentation

- Modify `web/tests/finopsMockApi.mjs`: decision snapshots, distinct evidence, remediation revisions, and request counters.
- Modify `web/tests/finops-operations-management.spec.mjs`: ROI/risk visual acceptance.
- Modify `web/tests/finops-portal-acceptance.spec.mjs`: remediation workflow and stale fallback.
- Create `web/tests/finops-decision-cache.spec.mjs`: no-repeat request and 10-minute refresh acceptance.
- Modify `docs/finops-portal.md`: update page purpose, refresh behavior, remediation boundary, and customer labels.
- Create `docs/validation/2026-07-31-operations-roi-risk-decision-candidate.md`: reproducible local/candidate evidence template populated only after commands pass.

---

### Task 1: Build Deterministic ROI And Risk Decision Projections

**Files:**
- Create: `backend/finops/decision_models.py`
- Create: `backend/finops/decision_service.py`
- Modify: `backend/finops/opportunities.py`
- Test: `tests/test_finops_decision_service.py`
- Test: `tests/test_finops_opportunities.py`

**Interfaces:**
- Consumes: existing ROI economics payload, workspace ROI snapshot, cost-value snapshot, day trends, anomalies, recommendations, opportunities, and latest stored insight.
- Produces:
  - `build_roi_decision(*, economics: Mapping[str, Any], roi_snapshot: Mapping[str, Any], cost_value: Mapping[str, Any], unit_trend: Sequence[Mapping[str, Any]]) -> dict[str, Any]`
  - `build_risk_decision(*, anomalies: Sequence[Mapping[str, Any]], opportunities: Sequence[Mapping[str, Any]], evidence_summaries: Sequence[Mapping[str, Any]], insight: Mapping[str, Any] | None, drafts: Sequence[Mapping[str, Any]], governance_capability: Mapping[str, Any]) -> dict[str, Any]`
  - `build_opportunity_queue(*, anomalies: list[Mapping[str, Any]], recommendations: list[Mapping[str, Any]], priced_cost: float | None, priced_coverage_pct: float | None, impact_estimates: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]`

- [ ] **Step 1: Write failing ROI decision tests**

```python
# tests/test_finops_decision_service.py
from backend.finops.decision_service import build_roi_decision


def test_roi_decision_keeps_scenario_separate_from_verified_value() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [
                {"id": "investment", "label": "投入", "value": 700.03, "unit": "USD", "status": "estimated"},
                {"id": "usage", "label": "使用", "value": 60, "unit": "调用", "status": "observed"},
                {"id": "output", "label": "产出", "value": 60, "unit": "分析", "status": "observed"},
                {"id": "outcome", "label": "业务结果", "value": None, "unit": "结果", "status": "not_recorded"},
            ],
            "scenarios": [{
                "scenario_id": "roi_scenario_demo",
                "status": "estimated",
                "result": {
                    "monthly_benefit": 3000,
                    "monthly_total_cost": 700.03,
                    "monthly_net_benefit": 2299.97,
                    "roi_ratio": 3.2856,
                    "payback_months": 2.1,
                    "formula_revision": "dataforge-roi-v1",
                },
            }],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={
            "lineage_complete": True,
            "usage": {"runs": 60},
            "observed_run_ids": ["run-001", "run-002"],
            "cost_evidence": {"status": "complete", "observed_run_ids": ["run-001", "run-002"]},
        },
        cost_value={
            "artifact_count": 12,
            "outcome_evidence": {
                "status": "not_recorded",
                "outcome_event_ids": [],
                "verified_outcome_event_ids": [],
            },
        },
        unit_trend=[],
    )

    assert result["decision"]["state"] == "scenario_positive_unverified"
    assert result["decision"]["title"] == "测算显示具备投入价值，业务结果仍需验证"
    assert result["metrics"][0]["status"] == "estimated"
    assert result["value_bridge"]["formula_revision"] == "dataforge-roi-v1"
    assert result["verified_roi"]["value"] is None
    assert result["evidence_maturity"]["score_pct"] == 75
    assert result["evidence_maturity"]["stages"][1]["evidence_count"] == 60
    assert result["evidence_maturity"]["stages"][2]["value"] == 12
    assert result["evidence_maturity"]["stages"][3]["evidence_gap"] == "业务结果尚未独立验证"
```

- [ ] **Step 2: Write failing risk and savings tests**

```python
from backend.finops.decision_service import build_risk_decision
from backend.finops.opportunities import build_opportunity_queue


def test_risk_decision_uses_impact_and_evidence_without_composite_score() -> None:
    result = build_risk_decision(
        anomalies=[{
            "anomaly_id": "anom_latency",
            "policy_type": "p95_latency",
            "severity": "warning",
            "sample_count": 60,
            "evidence_state": "observed",
            "evidence_refs": ["req_slow_000001"],
        }],
        opportunities=[{
            "opportunity_id": "opp_latency",
            "policy_type": "p95_latency",
            "impact": "high",
            "confidence": "high",
            "effort": "high",
            "sample_count": 60,
            "evidence_refs": ["req_slow_000001"],
            "estimated_savings": None,
            "currency": None,
        }],
        evidence_summaries=[{
            "request_ref": "req_slow_000001",
            "request_name": "演示工作区 · 自动分析 · 慢响应",
            "signal": {"metric": "latency_ms", "value": 6200, "unit": "ms"},
            "latency_ms": 6200,
            "cache_state": "miss",
            "status": "succeeded",
            "error_category": None,
            "technical_refs": {"request_ref": "req_slow_000001"},
        }],
        insight=None,
        drafts=[],
        governance_capability={
            "read_enabled": True,
            "draft_enabled": True,
            "actions_enabled": False,
            "typed_executors": ["cache_policy"],
        },
    )

    assert "risk_score" not in result
    assert result["risk_matrix"][0]["x_confidence"] == 3
    assert result["risk_matrix"][0]["y_impact"] == 3
    assert result["priorities"][0]["evidence_refs"] == ["req_slow_000001"]
    assert result["priorities"][0]["expected_impact"]["status"] == "unavailable"
    assert result["selected_evidence_summaries"][0]["request_name"].endswith("慢响应")
    assert result["portfolio_metadata"]["x_axis"] == "effort"
    assert result["governance_capability"]["actions_enabled"] is False


def test_opportunity_does_not_invent_savings_from_a_fixed_rate() -> None:
    items = build_opportunity_queue(
        anomalies=[{
            "anomaly_id": "anom_latency",
            "policy_type": "p95_latency",
            "severity": "warning",
            "sample_count": 60,
            "evidence_state": "observed",
            "evidence_refs": ["req_slow_000001"],
        }],
        recommendations=[],
        priced_cost=100,
        priced_coverage_pct=100,
        impact_estimates=None,
    )

    assert items[0]["estimated_savings"] is None
    assert items[0]["currency"] is None
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_finops_decision_service.py tests/test_finops_opportunities.py -q
```

Expected: FAIL because `decision_service` does not exist and `impact_estimates` is not accepted.

- [ ] **Step 4: Add typed decision models**

```python
# backend/finops/decision_models.py
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

DecisionEvidenceState = Literal[
    "observed", "estimated", "verified", "partial", "unavailable", "not_recorded"
]


class DecisionMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    value: float | int | None
    unit: str
    status: DecisionEvidenceState
    explanation: str


class DecisionStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    title: str
    summary: str
    evidence_state: DecisionEvidenceState


class RoiDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionStatement
    metrics: list[DecisionMetric]
    value_bridge: dict[str, Any]
    evidence_maturity: dict[str, Any]
    unit_economics_trend: list[dict[str, Any]]
    verified_roi: dict[str, Any]
    capability_explanation: dict[str, list[str]]
    scenarios: list[dict[str, Any]]
    evidence_gaps: list[str]


class RiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionStatement
    risk_domains: list[dict[str, Any]]
    risk_matrix: list[dict[str, Any]]
    priorities: list[dict[str, Any]]
    optimization_portfolio: list[dict[str, Any]]
    portfolio_metadata: dict[str, Any]
    selected_evidence_summaries: list[dict[str, Any]]
    insight: dict[str, Any] | None
    drafts: list[dict[str, Any]]
    governance_capability: dict[str, Any]
```

- [ ] **Step 5: Implement deterministic builders**

Implement `backend/finops/decision_service.py` with these exact rules:

```python
_LEVEL = {"low": 1, "medium": 2, "high": 3}
_COMPLETE_STAGE = {
    "investment": {"observed", "estimated", "verified", "complete"},
    "usage": {"observed", "verified", "complete"},
    "output": {"observed", "verified", "complete"},
    "outcome": {"verified", "complete"},
}


def _maturity(funnel: list[dict[str, Any]]) -> dict[str, Any]:
    stages = []
    score = 0
    for expected in ("investment", "usage", "output", "outcome"):
        raw = next((item for item in funnel if item.get("id") == expected), {})
        status = str(raw.get("status") or "unavailable")
        complete = status in _COMPLETE_STAGE[expected]
        score += 25 if complete else 0
        stages.append({
            "id": expected,
            "label": raw.get("label") or expected,
            "value": raw.get("value"),
            "unit": raw.get("unit") or "",
            "status": status,
            "evidence_count": max(0, int(raw.get("evidence_count") or 0)),
            "evidence_gap": str(raw.get("evidence_gap") or ""),
            "evidence_refs": [
                str(ref)
                for ref in (raw.get("evidence_refs") or [])
                if str(ref).strip()
            ][:20],
            "complete": complete,
        })
    return {"score_pct": score, "formula_revision": "roi-evidence-maturity-v1", "stages": stages}


def _roi_statement(scenario: dict[str, Any] | None, verified: dict[str, Any]) -> DecisionStatement:
    if verified.get("status") == "verified" and verified.get("value") is not None:
        return DecisionStatement(
            state="verified",
            title="已形成可复核 ROI",
            summary="成本与业务结果证据均已完成验证。",
            evidence_state="verified",
        )
    if scenario:
        return DecisionStatement(
            state="scenario_positive_unverified",
            title="测算显示具备投入价值，业务结果仍需验证",
            summary="情景参数与运行事实严格分开；验证完成前不显示已实现 ROI。",
            evidence_state="estimated",
        )
    return DecisionStatement(
        state="evidence_incomplete",
        title="已建立投入与使用证据，仍需补充价值假设",
        summary="当前范围不足以形成可复核 ROI。",
        evidence_state="partial",
    )
```

`build_roi_decision` must choose the highest scenario revision, create four monthly metrics from its server result, emit the exact capability lists from the design spec, construct `validated = RoiDecision.model_validate(result)`, and return `validated.model_dump(mode="json")`.

Before calling `_maturity`, enrich the four funnel stages deterministically:

- investment: evidence count and refs from priced/observed run IDs; gap names the unpriced-model or incomplete-cost condition;
- usage: value and count from `roi_snapshot.usage.runs`, refs from `observed_run_ids`;
- output: value and count from `cost_value.artifact_count`, with status `observed` when the field is present, including observed run refs;
- outcome: value and count from `outcome_evidence.outcome_event_ids`, refs limited to those event IDs, and gap `业务结果尚未独立验证` until every outcome is verified.

Do not place raw prompts, response text, identities, or provider IDs in these refs.

`build_risk_decision` must:

- map `impact/confidence` to numeric 1–3 coordinates;
- use `sample_count` as bubble size;
- group counts into cost, experience, efficiency, and governance domains;
- preserve each opportunity's own `evidence_refs`;
- return `selected_evidence_summaries` with risk-specific request name, signal value, request/result-cache state, error category, and folded technical IDs;
- return `portfolio_metadata` with `x_axis="effort"`, `y_axis="value_impact"`, `size="affected_scope"`, and `color="risk_domain"`;
- return `governance_capability` with current read, draft, action-gate, and typed-executor availability without exposing a cloud gateway product name;
- return `expected_impact.status="unavailable"` when no explicit estimate exists;
- validate with `RiskDecision`.

- [ ] **Step 6: Remove fixed-percentage savings**

Change `build_opportunity_queue` so monetary output is read only from:

```python
estimate = (impact_estimates or {}).get(policy_type) or {}
amount = estimate.get("amount")
estimated_savings = (
    round(float(amount), 8)
    if _finite_nonnegative(amount) and estimate.get("status") in {"estimated", "observed"}
    else None
)
```

Delete `_SAVINGS_RATE`. Keep cost coverage metadata for confidence but never multiply total cost by a policy constant.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
python -m pytest tests/test_finops_decision_service.py tests/test_finops_opportunities.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add backend/finops/decision_models.py backend/finops/decision_service.py backend/finops/opportunities.py tests/test_finops_decision_service.py tests/test_finops_opportunities.py
git commit -m "feat(finops): add ROI and risk decision projections"
```

---

### Task 2: Expose One Authorized Decision Endpoint Per Page

**Files:**
- Modify: `backend/control_plane.py`
- Modify: `backend/finops/query.py`
- Modify: `backend/finops/query_cache.py`
- Modify: `backend/finops/sql_rollups.py`
- Modify: `backend/finops/router.py`
- Create: `tests/test_finops_decision_api.py`
- Modify: `tests/test_finops_rollups.py`
- Modify: `tests/test_cost_value_api.py`

**Interfaces:**
- Consumes: Task 1 `build_roi_decision` and `build_risk_decision`.
- Produces:
  - `GET /api/finops/roi/decision`
  - `GET /api/finops/risk/decision`
  - `FinOpsQueryService.unit_economics_trend(query: FinOpsQuery, bucket: Literal["hour", "day"] = "day") -> dict[str, Any]`
  - `SqlFinOpsRollupRepository.read(query: FinOpsQuery, bucket_kind: Literal["hour", "day"]) -> list[FinOpsRollup]`
  - responses with the existing `scope/window/freshness/coverage/currency/data_status` envelope.

- [ ] **Step 1: Write failing API tests**

```python
# tests/test_finops_decision_api.py
def test_roi_decision_returns_one_composed_payload(client, owner_headers) -> None:
    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["workspace_ids"] == ["ws-a"]
    assert body["window"]["from"] == "2026-07-01T00:00:00Z"
    assert "decision" in body and "value_bridge" in body
    assert "provider_response_id" not in response.text


def test_risk_decision_does_not_trigger_agent(client, owner_headers, monkeypatch) -> None:
    called = False

    def fail_agent(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("read endpoint must not run an agent")

    monkeypatch.setattr("backend.finops.router.run_finops_analysis", fail_agent)
    response = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    assert called is False


def test_member_cannot_read_admin_decision_scope(client, member_headers) -> None:
    response = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-private"},
        headers=member_headers,
    )
    assert response.status_code == 403


def test_roi_decision_uses_rollup_unit_economics_when_sql_is_enabled(
    client, owner_headers, fake_sql_rollup_repository, monkeypatch
) -> None:
    monkeypatch.setattr(
        "backend.finops.router.get_finops_rollup_repository",
        lambda: fake_sql_rollup_repository,
    )
    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    assert fake_sql_rollup_repository.read_calls == [
        ("tenant-a", ("ws-a",), "2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z", "day")
    ]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_finops_decision_api.py -q
```

Expected: FAIL with 404 for both new routes.

- [ ] **Step 3: Add scoped rollup reads and the unit-economics projection**

In `backend/finops/sql_rollups.py`, add `read` with explicit predicates for `tenant_ref`, authorized workspace IDs, `bucket_at >= from`, and `bucket_at < to`. Select only aggregate columns required by `FinOpsRollup`; never select request facts or correlation identifiers. Reject an empty workspace scope before building SQL placeholders.

In `backend/finops/query.py`, add `rollup_repository: Any | None = None` to `FinOpsQueryService.__init__` and implement:

```python
def unit_economics_trend(
    self,
    query: FinOpsQuery,
    bucket: Literal["hour", "day"] = "day",
) -> dict[str, Any]:
    if self._rollup_repository is not None:
        rollups = self._rollup_repository.read(query, bucket)
    else:
        hourly, daily = aggregate_rollups(self._rows(query))
        rollups = hourly if bucket == "hour" else daily
    grouped: dict[str, list[FinOpsRollup]] = defaultdict(list)
    for item in rollups:
        grouped[item.bucket_at].append(item)
    items = []
    for bucket_at, rows in sorted(grouped.items()):
        requests = sum(item.request_count for item in rows)
        failures = min(requests, sum(item.failure_count for item in rows))
        successful = requests - failures
        known_costs = [
            item.estimated_cost for item in rows if item.estimated_cost is not None
        ]
        estimated_cost = round(sum(known_costs), 8) if known_costs else None
        items.append({
            "bucket_at": bucket_at,
            "successful_requests": successful,
            "estimated_cost": estimated_cost,
            "cost_per_successful_request": (
                round(estimated_cost / successful, 8)
                if estimated_cost is not None and successful > 0
                else None
            ),
            "data_status": (
                "available"
                if estimated_cost is not None and successful > 0
                else "unavailable"
            ),
        })
    return {"items": items, "count": len(items)}
```

Import `defaultdict`, `Literal`, `aggregate_rollups`, and `FinOpsRollup` explicitly. In `backend/finops/query_cache.py`, add a public `unit_economics_trend` wrapper that calls `_cached` with operation `unit_economics_trend` and bucket in `extras`. Cost remains `None` when every contributing row is unpriced, and failure count is never greater than request count.

For a query whose end time reaches the current UTC hour/day, read persisted rollups only through the last closed bucket, create a narrowed `FinOpsQuery` for the current incomplete bucket, aggregate only those request facts, and merge by `bucket_at`. For a historical closed window, use rollups only. Add tests proving the current bucket is not double-counted and a historical window does not read request facts. Wire `SqlFinOpsRollupRepository` into `get_finops_query_service` only when `DF_FINOPS_SQL_ENABLED=1`.

Add `test_sql_rollup_read_is_tenant_workspace_and_window_scoped` to `tests/test_finops_rollups.py`. Assert the captured SQL contains tenant, workspace, and both window predicates, and assert the returned row reconstructs as `FinOpsRollup`.

Apply rollup column predicates for department, agent, and model when those filters are present. Because the current rollup schema intentionally has no actor dimension, any query with `actor_ref` must aggregate authorized request facts instead of reading rollups. Add one test for each path.

In `backend/control_plane.py`, extend `workspace_cost_value_snapshot` with:

```python
"output_trend": [
    {
        "bucket_at": "2026-07-31",
        "effective_output_count": 3,
        "output_kind": "artifact",
        "data_status": "available",
    },
]
```

Build these rows from the same already-authorized artifact records used for `artifact_count`, grouped by their existing completed/task timestamp and filtered by the same ROI window. Return an empty list when no timestamped artifact exists. Test that out-of-window artifacts are excluded and no synthetic bucket is created.

- [ ] **Step 4: Add private loader helpers**

In `backend/finops/router.py`, add:

```python
def _roi_decision_payload(query_service: Any, query: FinOpsQuery) -> dict[str, Any]:
    roi = workspace_roi_snapshot(query.workspace_id, query.from_value, query.to_value)
    cost_value = workspace_cost_value_snapshot(query.workspace_id, query.from_value, query.to_value)
    economics = _roi_economics_payload(query_service, query, roi, cost_value)
    unit_trend = merge_output_trend(
        query_service.unit_economics_trend(query, "day").get("items") or [],
        cost_value.get("output_trend") or [],
    )
    decision = build_roi_decision(
        economics=economics,
        roi_snapshot=roi,
        cost_value=cost_value,
        unit_trend=unit_trend,
    )
    return _decision_envelope(query_service, query, decision)


def _risk_decision_payload(query_service: Any, query: FinOpsQuery) -> dict[str, Any]:
    events = query_service.events(query)
    anomaly_items = _decision_anomalies_from_events(events, tenant_ref=query.tenant_ref)
    opportunity_items = _decision_opportunities(query_service, query, anomaly_items)
    evidence_summaries = _risk_evidence_summaries(events, opportunity_items)
    latest = get_finops_insight_service().latest(
        tenant_ref=query.tenant_ref,
        authorized_workspace_ids=(query.workspace_id,),
        agent_kind="finops",
    )
    decision = build_risk_decision(
        anomalies=anomaly_items,
        opportunities=opportunity_items,
        evidence_summaries=evidence_summaries,
        insight=_public_insight(latest, include_evidence_refs=True) if latest else None,
        drafts=[],
        governance_capability=_governance_capability(),
    )
    return _decision_envelope(query_service, query, decision)
```

Reuse existing private helpers for ROI snapshot, anomalies, and authorization instead of duplicating formulas. `_risk_evidence_summaries` builds a request-ref map from the already-scoped `events`, selects at most two refs per opportunity and ten summaries total, and emits only the public request label, operation, model label, signal, cache state, status/error category, latency, cost status, and folded technical refs. It must not perform one SQL lookup per evidence ref.
`merge_output_trend` performs a left/full merge by `bucket_at`, preserving unavailable cost fields and unavailable output fields independently; it never distributes an aggregate count across days.

- [ ] **Step 5: Add routes**

```python
@router.get("/roi/decision")
async def roi_decision(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str = Query(min_length=1, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(
        request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model
    )
    if roles.get(workspace_id) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="ROI decision requires admin or owner")
    return _roi_decision_payload(service, query)


@router.get("/risk/decision")
async def risk_decision(
    request: Request,
    from_value: str | None = Query(default=None, alias="from", max_length=64),
    to_value: str | None = Query(default=None, alias="to", max_length=64),
    department_id: str | None = Query(default=None, max_length=128),
    workspace_id: str = Query(min_length=1, max_length=160),
    agent_id: str | None = Query(default=None, max_length=128),
    actor_ref: str | None = Query(default=None, max_length=128),
    model: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    service, query, roles = _common(
        request, from_value, to_value, department_id, workspace_id, agent_id, actor_ref, model
    )
    if roles.get(workspace_id) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="risk decision requires admin or owner")
    return _risk_decision_payload(service, query)
```

Both routes must derive tenant from `_common`, narrow to one authorized workspace, and return stored insight only.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest tests/test_finops_decision_api.py tests/test_finops_rollups.py tests/test_cost_value_api.py tests/test_finops_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/control_plane.py backend/finops/query.py backend/finops/query_cache.py backend/finops/sql_rollups.py backend/finops/router.py tests/test_finops_decision_api.py tests/test_finops_rollups.py tests/test_cost_value_api.py
git commit -m "feat(finops): expose ROI and risk decision APIs"
```

---

### Task 3: Add The Structured Remediation Draft Domain

**Files:**
- Create: `backend/finops/remediation.py`
- Create: `tests/test_finops_remediation.py`

**Interfaces:**
- Consumes: an authorized opportunity and current typed configuration version.
- Produces:
  - `RemediationDraft`
  - `FinOpsRemediationService.create/review/close/promote/list/get`
  - `RemediationDraftRepository` protocol.

- [ ] **Step 1: Write failing domain tests**

```python
# tests/test_finops_remediation.py
import pytest

from backend.finops.governance import FinOpsActionService, InMemoryActionRepository
from backend.finops.remediation import (
    FinOpsRemediationService,
    InMemoryRemediationDraftRepository,
    RemediationConflict,
)


def _service(current_version: str = "cache-policy-v1") -> FinOpsRemediationService:
    return FinOpsRemediationService(
        repository=InMemoryRemediationDraftRepository(),
        action_service=FinOpsActionService(
            repository=InMemoryActionRepository(),
            executors={},
        ),
        version_resolver=lambda tenant_ref, workspace_id, action_kind: (
            current_version if action_kind == "cache_policy" else "investigation-v1"
        ),
    )


def _opportunity(policy_type: str) -> dict[str, object]:
    return {
        "opportunity_id": f"opp-{policy_type}",
        "anomaly_id": f"anom-{policy_type}",
        "policy_type": policy_type,
        "title": "运营指标优化",
        "evidence_refs": [f"req-{policy_type}-000001"],
        "sample_count": 54,
    }


def test_cache_opportunity_creates_reviewable_draft_without_execution() -> None:
    service = _service()
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )

    assert draft.status == "draft"
    assert draft.action_kind == "cache_policy"
    assert draft.execution_capability == "typed_action_available"
    assert draft.proposed_changes[0]["field"] == "ttl_seconds"
    assert draft.proposed_changes[0]["candidate_value"] == 1800
    assert draft.translated_action_id is None


def test_investigation_draft_cannot_promote() -> None:
    service = _service()
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("error_rate"),
        base_version="investigation-v1",
    )
    reviewed = service.review(
        tenant_ref="tenant-a",
        draft_id=draft.draft_id,
        actor_ref="owner-b",
        base_revision=1,
    )
    with pytest.raises(RemediationConflict, match="advisory draft"):
        service.promote(
            tenant_ref="tenant-a",
            draft_id=reviewed.draft_id,
            actor_ref="owner-b",
            base_revision=2,
        )


def test_review_rejects_stale_revision() -> None:
    service = _service()
    draft = service.create(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        actor_ref="owner-a",
        opportunity=_opportunity("cache_hit_rate"),
        base_version="cache-policy-v1",
    )
    with pytest.raises(RemediationConflict, match="revision"):
        service.review(
            tenant_ref="tenant-a",
            draft_id=draft.draft_id,
            actor_ref="owner-b",
            base_revision=0,
        )


def test_create_rejects_stale_base_version_before_save() -> None:
    service = _service(current_version="cache-policy-v2")
    with pytest.raises(RemediationConflict, match="base version"):
        service.create(
            tenant_ref="tenant-a",
            workspace_id="ws-a",
            actor_ref="owner-a",
            opportunity=_opportunity("cache_hit_rate"),
            base_version="cache-policy-v1",
        )
    assert service.list(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
    ) == []
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
python -m pytest tests/test_finops_remediation.py -q
```

Expected: FAIL because `backend.finops.remediation` does not exist.

- [ ] **Step 3: Define strict models**

Implement these types in `backend/finops/remediation.py`:

```python
DraftStatus = Literal["draft", "reviewed", "pending_approval", "promoted", "closed"]
ActionKind = Literal[
    "cache_policy", "model_route", "price_mapping", "budget_notification", "investigation"
]
ExecutionCapability = Literal["advisory_only", "typed_action_available"]


class ProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal[
        "ttl_seconds", "enabled", "deployment", "price_mapping", "notification_threshold", "investigation_scope"
    ]
    current_value: bool | int | float | str | None
    candidate_value: bool | int | float | str
    rationale: str = Field(min_length=1, max_length=500)


class ExpectedImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float | None
    unit: Literal["USD", "percentage_point", "milliseconds", "requests"] | None
    status: Literal["observed", "estimated", "partial", "unavailable"]
    calculation_basis: str = Field(min_length=1, max_length=500)


class VerificationCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["cache_hit_rate_pct", "unit_cost", "result_consistency_pct", "success_rate_pct", "p95_latency_ms", "pricing_coverage_pct"]
    operator: Literal["gte", "lte", "no_worse_than_pct"]
    baseline_value: float | None
    baseline_window: str = Field(min_length=1, max_length=128)
    target: float
    candidate_window_minutes: int = Field(ge=5, le=10080)
    minimum_samples: int = Field(ge=20, le=100000)


class RemediationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    tenant_ref: str
    workspace_id: str
    source_opportunity_id: str
    source_anomaly_id: str | None
    risk_type: str
    title: str
    summary: str
    scope: dict[str, str | None]
    evidence_refs: list[str]
    proposed_changes: list[ProposedChange]
    expected_impact: ExpectedImpact
    prerequisites: list[str]
    risks_and_guardrails: list[str]
    verification_plan: list[VerificationCriterion]
    rollback_plan: list[str]
    action_kind: ActionKind
    execution_capability: ExecutionCapability
    base_version: str
    status: DraftStatus
    revision: int = Field(ge=1)
    created_by: str
    reviewed_by: str | None = None
    translated_action_id: str | None = None
    created_at: str
    updated_at: str
```

- [ ] **Step 4: Implement allowlisted templates**

Implement `_template(policy_type, workspace_id, base_version)` with exact safe outputs:

- `cache_hit_rate` → `cache_policy`, TTL candidate 1800 seconds, typed action available, hit-rate/unit-cost/result-consistency/P95 criteria.
- `p95_latency` → `investigation`, advisory only, model/batch comparison criteria; no deployment change without a separately selected typed route.
- `error_rate` → `investigation`, advisory only.
- `unpriced_requests` → `price_mapping`, advisory only until an administrator selects an official price revision through the existing settings workflow.
- `daily_cost_budget` → `budget_notification`, advisory only; it may link to the existing notification configuration but cannot become a quota action.
- all other policies → `investigation`, advisory only.

The service must ignore client-provided prose and build all fields from the template plus server opportunity evidence.
Set `typed_action_available` only when the action kind translates to one of the existing governance payload models and a server-owned translator is registered. Do not invent `price_mapping` or `budget_notification` governance payloads to make the UI promotable.
For typed drafts, resolve the current configuration version after selecting the template and before saving. A mismatch raises `RemediationConflict("base version changed")`; it must not create a draft. Investigation drafts still record the evidence revision supplied by the server opportunity loader.

- [ ] **Step 5: Implement revisioned service methods**

```python
def review(
    self,
    *,
    tenant_ref: str,
    draft_id: str,
    actor_ref: str,
    base_revision: int,
) -> RemediationDraft:
    draft = self._require(tenant_ref, draft_id)
    self._check_revision(draft, base_revision)
    if draft.status != "draft":
        raise RemediationConflict("only draft remediation may be reviewed")
    draft.status = "reviewed"
    draft.reviewed_by = actor_ref
    draft.revision += 1
    draft.updated_at = _now()
    return self._repository.save(draft)
```

`promote` must:

1. require status `reviewed` for the first attempt or `pending_approval` for a revision-checked retry;
2. reject `advisory_only`;
3. when status is `reviewed`, persist a `reviewed → pending_approval` revision and transition before translating;
4. translate only an allowlisted `action_kind`;
5. validate through the existing typed governance payload models;
6. call `FinOpsActionService.create(actor_kind="human")`;
7. persist `pending_approval → promoted`, store the new action ID, and return the promoted revision;
8. leave the draft in `pending_approval` with no action ID if action creation fails, so a retry must use the new revision;
9. never submit, approve, or execute the action.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest tests/test_finops_remediation.py tests/test_finops_governance.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/finops/remediation.py tests/test_finops_remediation.py
git commit -m "feat(finops): add structured remediation drafts"
```

---

### Task 4: Persist And Expose Remediation Drafts

**Files:**
- Create: `backend/finops/sql_remediation.py`
- Modify: `backend/sql/finops_schema.sql`
- Modify: `backend/finops/router.py`
- Create: `tests/test_finops_remediation_sql.py`
- Create: `tests/test_finops_remediation_api.py`
- Modify: `tests/test_finops_sql_migration.py`

**Interfaces:**
- Consumes: Task 3 repository protocol and service.
- Produces: additive SQL persistence and `/api/finops/remediation-drafts` routes.

- [ ] **Step 1: Write failing migration and repository tests**

```python
def test_finops_schema_contains_remediation_tables() -> None:
    sql = Path("backend/sql/finops_schema.sql").read_text(encoding="utf-8")
    assert "df_finops.remediation_draft" in sql
    assert "df_finops.remediation_transition" in sql
    assert "CK_finops_remediation_scope_json" in sql
    assert "CK_finops_remediation_status" in sql


def test_sql_remediation_repository_is_tenant_scoped(fake_connection_factory) -> None:
    repository = SqlRemediationDraftRepository(connection_factory=fake_connection_factory)
    saved = repository.save(_draft(tenant_ref="tenant-a"))
    assert repository.get("tenant-b", saved.draft_id) is None
    assert repository.get("tenant-a", saved.draft_id).draft_id == saved.draft_id
```

- [ ] **Step 2: Write failing API tests**

```python
def test_create_remediation_accepts_only_opportunity_and_version(client, owner_headers) -> None:
    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-cache",
            "base_version": "cache-policy-v1",
        },
        headers=owner_headers,
    )
    assert response.status_code == 201
    assert response.json()["draft"]["status"] == "draft"


def test_create_remediation_rejects_arbitrary_change_payload(client, owner_headers) -> None:
    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-cache",
            "base_version": "cache-policy-v1",
            "script": "remove-all",
        },
        headers=owner_headers,
    )
    assert response.status_code == 422


def test_create_remediation_returns_409_for_stale_base_version(
    client, owner_headers, monkeypatch
) -> None:
    monkeypatch.setattr(
        "backend.finops.router.current_remediation_base_version",
        lambda tenant_ref, workspace_id, action_kind: "cache-policy-v2",
    )
    response = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-cache",
            "base_version": "cache-policy-v1",
        },
        headers=owner_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "base version changed"


def test_promote_creates_draft_action_but_does_not_submit(
    client, owner_headers, second_owner_headers
) -> None:
    created = client.post(
        "/api/finops/remediation-drafts",
        json={
            "workspace_id": "ws-a",
            "source_opportunity_id": "opp-cache",
            "base_version": "cache-policy-v1",
        },
        headers=owner_headers,
    )
    draft = created.json()["draft"]
    reviewed = client.post(
        f"/api/finops/remediation-drafts/{draft['draft_id']}/review",
        json={"base_revision": draft["revision"]},
        headers=second_owner_headers,
    )
    reviewed_draft = reviewed.json()["draft"]
    response = client.post(
        f"/api/finops/remediation-drafts/{draft['draft_id']}/promote",
        json={"base_revision": reviewed_draft["revision"]},
        headers=second_owner_headers,
    )
    assert response.status_code == 200
    assert response.json()["draft"]["status"] == "promoted"
    assert response.json()["action"]["status"] == "draft"
```

- [ ] **Step 3: Run tests and verify they fail**

```powershell
python -m pytest tests/test_finops_remediation_sql.py tests/test_finops_remediation_api.py tests/test_finops_sql_migration.py -q
```

Expected: FAIL because tables, repository, and routes do not exist.

- [ ] **Step 4: Add additive SQL**

Add `df_finops.remediation_draft` with scalar identity/state columns and JSON columns for scope, evidence, changes, expected impact, prerequisites, guardrails, verification, and rollback. Add:

```sql
CONSTRAINT PK_finops_remediation_draft PRIMARY KEY (tenant_ref, draft_id),
CONSTRAINT CK_finops_remediation_status CHECK (
    draft_status IN (N'draft', N'reviewed', N'pending_approval', N'promoted', N'closed')
),
CONSTRAINT CK_finops_remediation_execution CHECK (
    execution_capability IN (N'advisory_only', N'typed_action_available')
),
CONSTRAINT CK_finops_remediation_scope_json CHECK (ISJSON(scope_json) = 1),
CONSTRAINT CK_finops_remediation_evidence_json CHECK (ISJSON(evidence_refs_json) = 1),
CONSTRAINT CK_finops_remediation_changes_json CHECK (ISJSON(proposed_changes_json) = 1),
CONSTRAINT CK_finops_remediation_revision CHECK (revision >= 1)
```

Add `df_finops.remediation_transition` with `(tenant_ref, workspace_id, draft_id, from_status, to_status, actor_ref, reason, occurred_at)` and an index on `(tenant_ref, workspace_id, occurred_at DESC)`.

- [ ] **Step 5: Implement SQL repository**

Follow `SqlFinOpsActionRepository`:

- `MERGE df_finops.remediation_draft WITH (HOLDLOCK) AS target` for `save`;
- tenant and draft ID in every query;
- JSON `ensure_ascii=False`, sorted compact encoding;
- transaction rollback wrapped as `FinOpsPersistenceError`;
- transition replacement or append in the same transaction;
- reconstruct through `RemediationDraft.model_validate`.

- [ ] **Step 6: Add service factory and strict request bodies**

In `backend/finops/router.py`, define:

```python
class RemediationDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=160)
    source_opportunity_id: str = Field(min_length=1, max_length=128)
    base_version: str = Field(min_length=1, max_length=128)


class RemediationTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=512)
```

Add repository selection matching other SQL-enabled services.

- [ ] **Step 7: Add routes and authorization**

All routes derive tenant and actor from trusted request context. Owner/admin is required. Create reloads the current authorized opportunity by ID and never accepts an opportunity body from the client.

Promotion returns both public draft and public action, with tenant fields excluded. Map stale revision to 409, missing draft to 404, advisory promotion to 409, and permission failure to 403.

After the repository is available, update `_risk_decision_payload` to call:

```python
drafts = get_finops_remediation_service().list(
    tenant_ref=query.tenant_ref,
    authorized_workspace_ids=(query.workspace_id,),
)
```

Pass only `draft.model_dump(mode="json", exclude={"tenant_ref", "created_by", "reviewed_by"})` into `build_risk_decision`. Add an API assertion that a saved draft appears only in its authorized workspace's next risk decision response.

- [ ] **Step 8: Run focused tests**

```powershell
python -m pytest tests/test_finops_remediation.py tests/test_finops_remediation_sql.py tests/test_finops_remediation_api.py tests/test_finops_sql_migration.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add backend/finops/sql_remediation.py backend/sql/finops_schema.sql backend/finops/router.py tests/test_finops_remediation_sql.py tests/test_finops_remediation_api.py tests/test_finops_sql_migration.py
git commit -m "feat(finops): persist and expose remediation drafts"
```

---

### Task 5: Add Server-Side Fresh/Stale Cache And Domain Revisions

**Files:**
- Modify: `backend/cache_store.py`
- Create: `backend/finops/cache_namespace.py`
- Modify: `backend/finops/query.py`
- Modify: `backend/finops/query_cache.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_finops_query_cache.py`
- Create: `tests/test_finops_cache_namespace.py`

**Interfaces:**
- Consumes: Redis JSON cache and authorized query scope.
- Produces:
  - `FinOpsCacheNamespace.current(tenant_ref, workspace_ids, domains) -> str`
  - `FinOpsCacheNamespace.bump(tenant_ref, workspace_id, domains) -> None`
  - `CachedFinOpsQueryService.bootstrap(query: FinOpsQuery, *, force_refresh: bool = False)`
  - `CachedFinOpsQueryService.overview(query: FinOpsQuery, *, force_refresh: bool = False)`
  - `CachedFinOpsQueryService.trends(query: FinOpsQuery, bucket: str, *, metric: str = "tokens", force_refresh: bool = False)`
  - `CachedFinOpsQueryService.compose(operation: Literal["roi_decision", "risk_decision"], query: FinOpsQuery, compute: Callable[[], dict[str, Any]], *, force_refresh: bool = False)`
  - response freshness status `hit_fresh|hit_stale|miss|revalidated|unavailable`.

- [ ] **Step 1: Write failing cache lifecycle tests**

```python
def test_stale_value_is_returned_without_recompute() -> None:
    clock = FakeClock("2026-07-31T00:06:00Z")
    cache = FakeJsonCache({
        "payload": {"metrics": {"requests": 60}},
        "_cache": {
            "generated_at": "2026-07-31T00:00:00Z",
            "fresh_until": "2026-07-31T00:05:00Z",
            "stale_until": "2026-07-31T00:30:00Z",
        },
    })
    delegate = RecordingQueryService()
    service = CachedFinOpsQueryService(delegate, cache=cache, clock=clock)

    result = service.bootstrap(_query())

    assert result["freshness"]["query_cache"]["status"] == "hit_stale"
    assert delegate.calls == []


def test_force_refresh_recomputes_and_replaces_stale_value() -> None:
    clock = FakeClock("2026-07-31T00:06:00Z")
    cache = FakeJsonCache({
        "payload": {"metrics": {"requests": 60}},
        "_cache": {
            "generated_at": "2026-07-31T00:00:00Z",
            "fresh_until": "2026-07-31T00:05:00Z",
            "stale_until": "2026-07-31T00:30:00Z",
        },
    })
    delegate = RecordingQueryService(
        bootstrap_result={"metrics": {"requests": 61}}
    )
    service = CachedFinOpsQueryService(delegate, cache=cache, clock=clock)
    result = service.bootstrap(_query(), force_refresh=True)
    assert result["freshness"]["query_cache"]["status"] == "revalidated"
    assert delegate.calls == ["bootstrap"]
    assert result["metrics"]["requests"] == 61


def test_namespace_revision_changes_only_selected_domain() -> None:
    namespace = FinOpsCacheNamespace(FakeAtomicCache())
    before = namespace.current("tenant-a", ("ws-a",), ("roi", "overview"))
    namespace.bump("tenant-a", "ws-a", ("roi",))
    assert namespace.current("tenant-a", ("ws-a",), ("roi", "overview")) != before
    assert namespace.current("tenant-a", ("ws-a",), ("risk",)) == "risk:ws-a:0"


def test_cache_key_changes_when_permission_scope_changes() -> None:
    owner_query = _query(permission_scope="roles-owner-6e3806")
    member_query = _query(permission_scope="roles-member-2f344a")
    assert _cache_key("overview", owner_query, {}, "overview:ws-a:0") != _cache_key(
        "overview", member_query, {}, "overview:ws-a:0"
    )
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
python -m pytest tests/test_finops_query_cache.py tests/test_finops_cache_namespace.py -q
```

Expected: FAIL because the new constructor and namespace service do not exist.

- [ ] **Step 3: Add bounded atomic Redis helpers**

In `backend/cache_store.py`, add:

```python
def increment(key: str, *, ttl_seconds: int = 86400 * 30) -> tuple[int | None, dict[str, Any]]:
    client, meta = _client()
    if client is None:
        return None, meta
    try:
        value = int(client.incr(key))
        client.expire(key, ttl_seconds)
        return value, meta | {"status": "incremented"}
    except Exception as exc:
        return None, meta | {"status": "unavailable", "error": _err(exc)}


def acquire_lock(key: str, token: str, *, ttl_seconds: int = 30) -> tuple[bool, dict[str, Any]]:
    client, meta = _client()
    if client is None:
        return False, meta
    try:
        acquired = bool(client.set(key, token, nx=True, ex=ttl_seconds))
        return acquired, meta | {"status": "acquired" if acquired else "busy"}
    except Exception as exc:
        return False, meta | {"status": "unavailable", "error": _err(exc)}
```

Release locks with a compare-and-delete Lua script so one request cannot delete another request's lock.

- [ ] **Step 4: Implement cache namespaces**

`backend/finops/cache_namespace.py` must:

- use hashed tenant/workspace/domain keys;
- combine every selected workspace revision in stable sorted order;
- return `domain:workspace:0` for each selected workspace when Redis is unavailable;
- increment only specified domains;
- never scan Redis keys;
- expose domains `overview`, `cost`, `roi`, `risk`, `requests`, `settings`.

Include the namespace revision string in `_cache_key`.

Add `permission_scope: str` to `FinOpsQuery`. In `_context`, calculate it as the first 16 hex characters of SHA-256 over sorted `(workspace_id, role)` pairs from trusted authorization, never over actor identity or a token. Include it in query serialization and add a cross-role isolation test.

- [ ] **Step 5: Store a 30-minute envelope**

In `backend/finops/query_cache.py`:

- remove the 300-second hard maximum;
- store Redis entries for 1800 seconds;
- record `generated_at`, `fresh_until=+300s`, and `stale_until=+1800s`;
- return stale immediately when not forced;
- on `force_refresh=True`, acquire the per-key lock, recompute once, and store a new envelope;
- when a force request finds the lock busy and stale data exists, return stale with status `revalidating`;
- when Redis is unavailable, call the delegate and report `unavailable`.
- cache the complete ROI or risk decision payload through `compose`, using the operation name and namespace revision in the key, so one Portal page maps to one Redis object;
- expose the uncached delegate as a private implementation detail only; route code supplies a pure `compute` callback and never bypasses tenant/workspace authorization.

- [ ] **Step 6: Add route revalidation and invalidation**

Decision read endpoints accept:

```python
refresh: bool = Query(default=False)
```

Wrap each complete decision payload with `service.compose`; pass `refresh` as `force_refresh` so `refresh=1` bypasses a fresh or stale object only after authorization succeeds. After successful writes:

- ROI scenario/result verification → bump `roi,overview`;
- anomaly acknowledge/suppress → bump `risk`;
- remediation create/review/promote/close → bump `risk`;
- price mapping/activation → bump `cost,roi,risk,overview`;
- cache/model route action transition → bump `cost,roi,risk,overview`.

Do not invalidate on failed writes.

- [ ] **Step 7: Run focused tests**

```powershell
python -m pytest tests/test_finops_query_cache.py tests/test_finops_cache_namespace.py tests/test_finops_decision_api.py tests/test_finops_remediation_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add backend/cache_store.py backend/finops/cache_namespace.py backend/finops/query.py backend/finops/query_cache.py backend/finops/router.py tests/test_finops_query_cache.py tests/test_finops_cache_namespace.py
git commit -m "perf(finops): add stale query cache and scoped invalidation"
```

---

### Task 6: Add The Browser FinOps Data Store And API Clients

**Files:**
- Create: `web/src/finopsDataStore.js`
- Create: `web/src/finopsDataStore.test.mjs`
- Modify: `web/src/api.js`
- Modify: `web/src/finopsPreload.js`
- Modify: `web/src/finopsPreload.test.mjs`
- Test: `web/src/finopsApi.test.mjs`

**Interfaces:**
- Consumes: API loader functions that accept `{ signal, refresh }`.
- Produces:
  - `finopsDataKey(scope)`
  - `readFinOpsData(key, now?)`
  - `loadFinOpsData(key, loader, { domain, force, now }?)`
  - `invalidateFinOpsData(predicate)`
  - `clearFinOpsData()`
  - API functions `loadFinOpsRoiDecision`, `loadFinOpsRiskDecision`, and remediation CRUD.

- [ ] **Step 1: Write failing data-store tests**

```javascript
// web/src/finopsDataStore.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {
  loadFinOpsData,
  readFinOpsData,
  invalidateFinOpsData,
  clearFinOpsData,
} from "./finopsDataStore.js";

test("fresh entries render without another request", async () => {
  clearFinOpsData();
  let calls = 0;
  const loader = async () => ({ decision: { title: "ready" } });
  await loadFinOpsData(
    "tenant/ws/roi",
    async () => { calls += 1; return loader(); },
    { domain: "roi", now: 1_000 },
  );
  const value = await loadFinOpsData(
    "tenant/ws/roi",
    async () => { calls += 1; return loader(); },
    { domain: "roi", now: 20_000 },
  );
  assert.equal(calls, 1);
  assert.equal(value.decision.title, "ready");
  assert.equal(readFinOpsData("tenant/ws/roi", 20_000).status, "fresh");
});

test("stale entries remain visible while one request revalidates", async () => {
  clearFinOpsData();
  await loadFinOpsData(
    "tenant/ws/risk",
    async () => ({ revision: 1 }),
    { domain: "risk", now: 0 },
  );
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const first = loadFinOpsData(
    "tenant/ws/risk",
    () => pending,
    { domain: "risk", force: true, now: 360_001 },
  );
  const second = loadFinOpsData("tenant/ws/risk", () => {
    throw new Error("duplicate request");
  }, { domain: "risk", force: true, now: 360_001 });
  assert.equal(readFinOpsData("tenant/ws/risk", 360_001).value.revision, 1);
  resolve({ revision: 2 });
  assert.deepEqual(await Promise.all([first, second]), [{ revision: 2 }, { revision: 2 }]);
});

test("domain invalidation removes ROI without removing risk", async () => {
  clearFinOpsData();
  const roiKey = "tenant/ws/roi";
  const riskKey = "tenant/ws/risk";
  await loadFinOpsData(
    roiKey,
    async () => ({ revision: 1 }),
    { domain: "roi", now: 1_000 },
  );
  await loadFinOpsData(
    riskKey,
    async () => ({ revision: 1 }),
    { domain: "risk", now: 1_000 },
  );
  invalidateFinOpsData((entry) => entry.domain === "roi");
  assert.equal(readFinOpsData(roiKey, 1_000).status, "missing");
  assert.equal(readFinOpsData(riskKey, 1_000).status, "fresh");
});
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
Set-Location web
node --test src/finopsDataStore.test.mjs src/finopsPreload.test.mjs src/finopsApi.test.mjs
```

Expected: FAIL because `finopsDataStore.js` and new API exports do not exist.

- [ ] **Step 3: Implement the in-memory store**

Use:

```javascript
const FRESH_MS = 300_000;
const STALE_MS = 1_800_000;
const entries = new Map();

export function finopsDataKey({
  tenantScope,
  permissionSummary,
  workspaceId,
  domain,
  from = "",
  to = "",
  filters = {},
  schemaRevision = "finops-decision-v1",
}) {
  const normalizedFilters = Object.entries(filters)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .sort(([left], [right]) => left.localeCompare(right));
  return JSON.stringify({
    tenantScope: String(tenantScope || ""),
    permissionSummary: String(permissionSummary || ""),
    workspaceId: String(workspaceId || ""),
    domain: String(domain || ""),
    from: String(from || ""),
    to: String(to || ""),
    schemaRevision,
    filters: normalizedFilters,
  });
}

export function readFinOpsData(key, now = Date.now()) {
  const entry = entries.get(key);
  if (!entry?.value) return { status: "missing", value: null };
  const age = Math.max(0, now - entry.storedAt);
  if (age <= FRESH_MS) return { status: "fresh", value: entry.value };
  if (age <= STALE_MS) return { status: "stale_usable", value: entry.value };
  return { status: "expired", value: null };
}
```

`loadFinOpsData` must:

- store `domain`, `storedAt`, `value`, `inFlight`, `abortController`, and `lastError` on each entry;
- return fresh value without calling loader;
- return the same `inFlight` promise for duplicate requests;
- retain old value while forced revalidation runs;
- retain old value after revalidation failure and record `lastError`;
- validate that payload is a non-array object;
- abort and delete entries on `clearFinOpsData`;
- never use `localStorage` or `sessionStorage`.

`tenantScope` must be the trusted tenant reference already projected by the authenticated application context, and `permissionSummary` must be a stable sorted workspace-role summary, never a raw token or user identity. Task 10 must clear all entries when either value changes.

- [ ] **Step 4: Add API clients**

In `web/src/api.js` add:

```javascript
export function loadFinOpsRoiDecision(filters = {}, options = {}) {
  return loadFinOpsResource("roi/decision", filters, options);
}

export function loadFinOpsRiskDecision(filters = {}, options = {}) {
  return loadFinOpsResource("risk/decision", filters, options);
}

export function createFinOpsRemediationDraft(payload, options = {}) {
  return request("/api/finops/remediation-drafts", Object.assign({}, options, {
    method: "POST",
    body: JSON.stringify(payload),
  }));
}
```

Add list/get/review/promote/close functions. When `options.refresh === true`, append `refresh=1`; do not add arbitrary headers.

- [ ] **Step 5: Preserve bootstrap compatibility**

Refactor `finopsPreload.js` to call the new store using domain `overview`, while keeping current exports `readFinOpsBootstrap`, `prefetchFinOpsBootstrap`, and `clearFinOpsBootstrap` so `App.jsx` remains compatible until Task 10.

- [ ] **Step 6: Run focused tests**

```powershell
Set-Location web
node --test src/finopsDataStore.test.mjs src/finopsPreload.test.mjs src/finopsApi.test.mjs
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add web/src/finopsDataStore.js web/src/finopsDataStore.test.mjs web/src/api.js web/src/finopsPreload.js web/src/finopsPreload.test.mjs web/src/finopsApi.test.mjs
git commit -m "perf(web): add cache-first FinOps data store"
```

---

### Task 7: Add Honest Decision View Models And Shared BI Components

**Files:**
- Create: `web/src/finopsDecisionViewModel.js`
- Create: `web/src/finopsDecisionViewModel.test.mjs`
- Create: `web/src/finops/DecisionCharts.jsx`
- Create: `web/src/finops/FinOpsCapabilityNote.jsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: Task 2 decision API payloads.
- Produces:
  - `roiDecisionView(payload)`
  - `riskDecisionView(payload)`
  - `remediationDraftView(payload)`
  - `ValueBridge`, `EvidenceMaturity`, `RiskMatrix`, `OpportunityPortfolio`, `FinOpsCapabilityNote`.

- [ ] **Step 1: Write failing view-model tests**

```javascript
test("ROI view never labels an estimated scenario as verified", () => {
  const view = roiDecisionView({
    decision: { state: "scenario_positive_unverified", title: "测算显示具备投入价值，业务结果仍需验证" },
    metrics: [{ id: "monthly_net", value: 2299.97, unit: "USD", status: "estimated" }],
    verified_roi: { value: null, status: "not_recorded" },
    value_bridge: { items: [], formula_revision: "dataforge-roi-v1" },
    evidence_maturity: { score_pct: 75, stages: [] },
  });
  assert.equal(view.metrics[0].badge, "情景测算");
  assert.equal(view.verifiedRoiLabel, "证据不足");
});

test("risk bubbles preserve real differences and unavailable savings", () => {
  const view = riskDecisionView({
    risk_matrix: [
      { id: "a", x_confidence: 3, y_impact: 3, bubble_size: 60 },
      { id: "b", x_confidence: 2, y_impact: 1, bubble_size: 20 },
    ],
    priorities: [{ id: "a", expected_impact: { amount: null, status: "unavailable" } }],
  });
  assert.notEqual(view.matrix[0].radius, view.matrix[1].radius);
  assert.equal(view.priorities[0].impactLabel, "待验证");
});

test("zero is preserved while missing remains unavailable", () => {
  const view = roiDecisionView({ metrics: [
    { id: "cost", value: 0, unit: "USD", status: "observed" },
    { id: "value", value: null, unit: "USD", status: "unavailable" },
  ]});
  assert.equal(view.metrics[0].valueLabel, "$0.00");
  assert.equal(view.metrics[1].valueLabel, "暂不可用");
});
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
Set-Location web
node --test src/finopsDecisionViewModel.test.mjs
```

Expected: FAIL because the view-model module does not exist.

- [ ] **Step 3: Implement pure view models**

The view model must:

- use server coordinates and formula revisions;
- clamp only visual radius, not source values;
- preserve zero;
- map evidence states through one shared label table;
- expose chart descriptions for screen readers;
- return empty arrays for invalid collections;
- never calculate ROI or savings.

- [ ] **Step 4: Implement accessible chart primitives**

`DecisionCharts.jsx` requirements:

- `ValueBridge` uses proportional bars and a text table fallback;
- `EvidenceMaturity` exposes stage label, value, unit, and status;
- `RiskMatrix` uses keyboard-focusable buttons for points and calls `onSelect(id)`;
- `OpportunityPortfolio` exposes both the scatter and an ordered list;
- tooltips open on hover, focus, and tap;
- `prefers-reduced-motion` disables translation/scaling animations;
- charts render `EmptyState` when no valid values exist.

`FinOpsCapabilityNote.jsx` renders the server-provided platform-confirmed list, business-verification list, and governance boundary in a compact disclosure. It never hard-codes a claim that a business result is verified and never displays cloud gateway product names.

- [ ] **Step 5: Add focused CSS**

Add only classes prefixed `finops-decision-` or `finops-remediation-`. Use existing color tokens, 1px borders, and current typography. Do not introduce a new chart dependency.

- [ ] **Step 6: Run tests and build**

```powershell
Set-Location web
node --test src/finopsDecisionViewModel.test.mjs
npm run build
```

Expected: tests PASS and Vite build succeeds.

- [ ] **Step 7: Commit**

```powershell
git add web/src/finopsDecisionViewModel.js web/src/finopsDecisionViewModel.test.mjs web/src/finops/DecisionCharts.jsx web/src/finops/FinOpsCapabilityNote.jsx web/src/styles.css
git commit -m "feat(web): add FinOps decision BI components"
```

---

### Task 8: Replace The ROI Page With The Decision Layout

**Files:**
- Create: `web/src/finops/RoiDecisionPage.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/finopsLayout.test.mjs`
- Test: `web/src/finopsDecisionViewModel.test.mjs`

**Interfaces:**
- Consumes: Task 6 `loadFinOpsRoiDecision`, Task 7 `roiDecisionView` and shared charts.
- Produces: `RoiDecisionPage({ payload, loading, updating, error, onAdjustScenario, onEvidence, onAsk })`.

- [ ] **Step 1: Write failing source/layout assertions**

```javascript
test("ROI page exposes the approved decision hierarchy", () => {
  const source = readFileSync(new URL("./finops/RoiDecisionPage.jsx", import.meta.url), "utf8");
  assert.match(source, /本期经营判断/);
  assert.match(source, /价值桥接/);
  assert.match(source, /证据成熟度/);
  assert.match(source, /单位效能趋势/);
  assert.match(source, /平台自动确认/);
  assert.match(source, /业务侧补充验证/);
  assert.doesNotMatch(source, /Azure Cost Management/);
});
```

- [ ] **Step 2: Run the test and verify it fails**

```powershell
Set-Location web
node --test src/finopsLayout.test.mjs
```

Expected: FAIL because `RoiDecisionPage.jsx` does not exist.

- [ ] **Step 3: Implement ROI page**

Use this fixed order:

1. decision banner;
2. four monthly metrics from the same scenario/window;
3. value bridge and evidence maturity;
4. unit-economics trend and capability explanation;
5. compact AI capability strip.

`updating` renders a non-shifting inline label. `error` with existing payload renders a stale warning; without payload it renders a local retry state. Evidence buttons pass the stage's own refs.

- [ ] **Step 4: Integrate without retaining duplicate ROI cards**

In `FinOpsPortal.jsx`:

- remove the old `RoiEconomics` and `RoiPage` bodies after the new component is wired;
- keep `RoiScenarioDialog`;
- load one ROI decision payload instead of three parallel ROI calls;
- after scenario save, invalidate `roi` and force only the ROI decision request;
- do not re-fetch overview unless its ROI summary is visible.

- [ ] **Step 5: Run Node tests and build**

```powershell
Set-Location web
node --test src/finopsLayout.test.mjs src/finopsDecisionViewModel.test.mjs src/finopsApi.test.mjs
npm run build
```

Expected: PASS and build succeeds.

- [ ] **Step 6: Commit**

```powershell
git add web/src/finops/RoiDecisionPage.jsx web/src/FinOpsPortal.jsx web/src/styles.css web/src/finopsLayout.test.mjs
git commit -m "feat(web): rebuild ROI as an operating decision page"
```

---

### Task 9: Replace The Risk Page And Add Remediation Draft UI

**Files:**
- Create: `web/src/finops/RiskDecisionPage.jsx`
- Create: `web/src/finops/RemediationDraftPanel.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/finopsLayout.test.mjs`
- Modify: `web/src/finopsInteraction.test.mjs`

**Interfaces:**
- Consumes: Task 6 risk/remediation API functions and Task 7 risk/remediation view models.
- Produces:
  - `RiskDecisionPage({ payload, selectedRiskId, onSelectRisk, onEvidence, onCreateDraft, onAsk })`
  - `RemediationDraftPanel({ draft, busy, error, onClose, onCreate, onReview, onPromote })`.

- [ ] **Step 1: Write failing layout and interaction tests**

```javascript
test("risk page contains the complete signal-to-verification chain", () => {
  const source = readFileSync(new URL("./finops/RiskDecisionPage.jsx", import.meta.url), "utf8");
  for (const label of ["风险矩阵", "优先事项", "优化组合", "信号", "影响范围", "代表证据", "改善验证"]) {
    assert.match(source, new RegExp(label));
  }
});

test("remediation panel makes save distinct from execution", () => {
  const source = readFileSync(new URL("./finops/RemediationDraftPanel.jsx", import.meta.url), "utf8");
  assert.match(source, /保存整改草案/);
  assert.match(source, /不会直接执行/);
  assert.doesNotMatch(source, /一键执行/);
});
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
Set-Location web
node --test src/finopsLayout.test.mjs src/finopsInteraction.test.mjs
```

Expected: FAIL because the new components do not exist.

- [ ] **Step 3: Implement risk decision page**

Render:

1. risk decision banner and four domains;
2. linked risk matrix and priority list;
3. opportunity portfolio;
4. selected risk's five-stage evidence chain;
5. inline latest stored AI explanation;
6. compact governance boundary.

Selecting a matrix point changes the priority/evidence region without fetching a different generic request. “查看证据” sends that priority's `evidence_refs`.

The evidence region uses `selected_evidence_summaries` first. It shows the customer-readable request name, signal, cache/error/result summary, and final visible answer summary allowed by the current evidence projection. Technical request, run, and trace IDs stay inside a collapsed “技术详情” disclosure; provider response IDs, prompts, raw identities, and internal errors never render.

- [ ] **Step 4: Implement remediation panel**

The panel shows:

- source evidence;
- scope;
- allowlisted proposed changes;
- expected impact state;
- prerequisites;
- guardrails;
- verification criteria;
- rollback plan;
- five-step lifecycle.

Create sends only workspace, opportunity ID, and base version. Review/promote sends current revision. A 409 keeps the panel open, reloads the latest draft, and displays “方案已更新，请重新复核”.

Promotion is hidden for `advisory_only`. When actions are disabled, promotion may create a draft action but no execute control appears.

- [ ] **Step 5: Integrate and remove duplicate old lists**

In `FinOpsPortal.jsx`:

- replace four risk endpoint calls with one risk decision request;
- remove the old `RiskPage` body after integration;
- preserve anomaly acknowledge/suppress via the selected priority where applicable;
- open remediation panel from “查看整改方案”;
- invalidate only `risk` after draft/anomaly changes;
- keep evidence drawer and AI popover.

- [ ] **Step 6: Run Node tests and build**

```powershell
Set-Location web
node --test src/finopsLayout.test.mjs src/finopsInteraction.test.mjs src/finopsDecisionViewModel.test.mjs
npm run build
```

Expected: PASS and build succeeds.

- [ ] **Step 7: Commit**

```powershell
git add web/src/finops/RiskDecisionPage.jsx web/src/finops/RemediationDraftPanel.jsx web/src/FinOpsPortal.jsx web/src/styles.css web/src/finopsLayout.test.mjs web/src/finopsInteraction.test.mjs
git commit -m "feat(web): add evidence-backed risk remediation flow"
```

---

### Task 10: Integrate Cache-First Navigation And Ten-Minute Refresh

**Files:**
- Modify: `web/src/App.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsNavigation.js`
- Modify: `web/src/finopsNavigation.test.mjs`
- Modify: `web/src/finopsPreload.test.mjs`
- Test: `web/src/finopsDataStore.test.mjs`

**Interfaces:**
- Consumes: Task 6 data store and decision API loaders.
- Produces: immediate cached render, tab intent prefetch, visible-tab refresh, manual force refresh, and scoped invalidation.

- [ ] **Step 1: Write failing lifecycle tests**

```javascript
test("portal mount consumes stale bootstrap without force loading", () => {
  const source = readFileSync(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /prefetchFinOpsBootstrap\\([^)]*force:\\s*true/s);
});

test("automatic refresh is ten minutes", () => {
  assert.equal(FINOPS_REFRESH_MS, 600_000);
});

test("tab intent prefetches only the selected decision resource", async () => {
  const calls = [];
  await prefetchFinOpsTab("roi", loaders(calls));
  assert.deepEqual(calls, ["roi"]);
});
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
Set-Location web
node --test src/finopsNavigation.test.mjs src/finopsPreload.test.mjs src/finopsDataStore.test.mjs
```

Expected: FAIL because the current mount forces a request and refresh is 300000 ms.

- [ ] **Step 3: Remove forced mount revalidation**

On mount:

- fresh → render, no request;
- stale-usable → render, start one background revalidation;
- expired/missing → local skeleton and request;
- keep the last successful payload during every update.

Do not clear the cache merely because `FinOpsPortal` unmounts. Clear only when tenant, identity permission summary, or workspace authorization changes.

- [ ] **Step 4: Add tab-intent prefetch**

Attach `onPointerEnter`, `onFocus`, and `onTouchStart` to each tab. Use one `prefetchFinOpsTab(tab, scope)` helper:

- overview → bootstrap;
- cost → current cost loader result cached as domain `cost`;
- roi → ROI decision;
- risk → risk decision.

After overview first paint, use `requestIdleCallback` with a timeout fallback to prefetch ROI only. Do not preload every detail endpoint.

- [ ] **Step 5: Implement visible-tab refresh**

Set `FINOPS_REFRESH_MS = 600_000`. Track `lastSuccessfulAt` per tab:

- hidden pages do not refresh;
- visibility restore refreshes only if current tab is at least 10 minutes old;
- timer forces only the current tab;
- comparison data loads only while comparison is enabled;
- manual refresh forces current tab immediately;
- the update label uses fixed-width layout so it cannot shift the header.

- [ ] **Step 6: Implement scoped invalidation**

Use `invalidateFinOpsData`:

- ROI scenario save → domain `roi`;
- risk draft/anomaly change → domain `risk`;
- saved cost view → domain `cost`;
- price/model/cache setting change callbacks → exact affected domains from Global Constraints.

- [ ] **Step 7: Run focused tests and build**

```powershell
Set-Location web
node --test src/finopsNavigation.test.mjs src/finopsPreload.test.mjs src/finopsDataStore.test.mjs src/finopsLayout.test.mjs
npm run build
```

Expected: PASS and build succeeds.

- [ ] **Step 8: Commit**

```powershell
git add web/src/App.jsx web/src/FinOpsPortal.jsx web/src/finopsNavigation.js web/src/finopsNavigation.test.mjs web/src/finopsPreload.test.mjs web/src/finopsDataStore.test.mjs
git commit -m "perf(web): make operations navigation cache first"
```

---

### Task 11: Add Browser Acceptance, Distinct Evidence, And Documentation

**Files:**
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`
- Create: `web/tests/finops-decision-cache.spec.mjs`
- Modify: `docs/finops-portal.md`
- Create: `docs/validation/2026-07-31-operations-roi-risk-decision-candidate.md`

**Interfaces:**
- Consumes: completed backend and frontend features.
- Produces: reproducible browser, build, regression, and candidate evidence.

- [ ] **Step 1: Extend the mock API with request counters and decision payloads**

Add counters:

```javascript
control.calls = {
  bootstrap: 0,
  roiDecision: 0,
  riskDecision: 0,
  remediationCreate: 0,
};
```

Return:

- ROI scenario values with `estimated` state and unverified outcome;
- four different risk points and distinct request refs;
- one cache draft template;
- 409 once mode for remediation revision conflict;
- delayed and failed revalidation modes while retaining previous successful payload.

- [ ] **Step 2: Write ROI and risk visual tests**

Add Playwright assertions:

```javascript
await page.getByRole("button", { name: "效能与 ROI" }).click();
await expect(page.getByText("测算显示具备投入价值，业务结果仍需验证")).toBeVisible();
await expect(page.getByText("价值桥接")).toBeVisible();
await expect(page.getByText("证据成熟度")).toBeVisible();
await expect(page.getByText("平台自动确认")).toBeVisible();

await page.getByRole("button", { name: "风险与优化" }).click();
await expect(page.getByText("风险矩阵")).toBeVisible();
await page.getByRole("button", { name: "响应时延" }).click();
await expect(page.getByText("6.2 秒")).toBeVisible();
await page.getByRole("button", { name: "缓存效率" }).click();
await expect(page.getByText("缓存未命中")).toBeVisible();
```

Take desktop and mobile screenshots into `output/playwright/`, but keep them untracked unless the repository's validation convention explicitly tracks selected images.

- [ ] **Step 3: Write remediation acceptance**

```javascript
await page.getByRole("button", { name: "查看整改方案" }).first().click();
await expect(page.getByText("不会直接执行")).toBeVisible();
await page.getByRole("button", { name: "保存整改草案" }).click();
await expect(page.getByText("整改草案")).toBeVisible();
await expect(page.getByRole("button", { name: "候选执行" })).toHaveCount(0);
```

Add a 409 test that reloads the revision and requires a new review.

- [ ] **Step 4: Write cache acceptance**

Use Playwright's clock support or the existing fake timer harness:

1. enter Portal and wait for bootstrap;
2. switch ROI → risk → ROI inside 5 minutes;
3. assert each decision endpoint count remains 1;
4. advance to 10 minutes with page visible;
5. assert only current tab count increments;
6. hide page and advance another 10 minutes;
7. assert no increment;
8. force a failed refresh and assert old metric remains visible with stale warning;
9. click manual refresh and assert current tab increments.

- [ ] **Step 5: Add performance and visual-regression acceptance**

Add browser timing markers around navigation intent and the first interactive decision control. With a seeded browser cache, assert the ROI/risk page becomes interactive within 300 ms in the Playwright environment and does not show the full-page skeleton. Record, but do not hard-fail on, cold backend latency because candidate infrastructure variance is external to the UI contract.

Add server test instrumentation showing two concurrent forced requests for the same tenant/permission/workspace/filter key call the decision compute callback once. Capture separate validation rows for cold cache, Redis fresh hit, Redis stale response plus revalidation, and browser fresh cache.

At desktop, 1366px, and mobile widths, assert:

- the fixed-height update label does not change the header bounding box;
- tooltip content opens on hover and keyboard focus;
- the evidence/remediation surface stays below the page header and inside the viewport;
- no element marked as a decorative status dot remains visible;
- help buttons sit inline with their label and have an accessible name;
- chart bars and bubbles have at least two distinct geometry values when source values differ.

Capture screenshots after each assertion group and inspect them before recording acceptance.

- [ ] **Step 6: Update documentation**

`docs/finops-portal.md` must state:

- four operating questions;
- ROI scenario/verified boundary;
- risk-to-remediation flow;
- 10-minute visible-tab refresh;
- 5/30-minute client and server cache;
- no automatic Agent call;
- no production execution from draft save;
- customer-facing “统一入口” terminology.

Populate validation evidence with exact commands and actual results only. Do not pre-fill pass counts.

- [ ] **Step 7: Run full verification**

Run from repository root:

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
npx playwright test
Set-Location ..
git diff --check
```

Expected:

- all Python tests pass, with only the repository's known intentional skip;
- all Node tests pass;
- Vite build succeeds;
- all Playwright tests pass;
- `git diff --check` returns no output.

- [ ] **Step 8: Review runtime boundaries**

Confirm:

```powershell
git status --short
git diff --name-only origin/main HEAD
```

Verify:

- no secrets, tokens, production responses, `.superpowers/brainstorm/`, `test-results/`, `web/test-results/`, or untracked workspaces are staged;
- `DF_FINOPS_ACTIONS_ENABLED` default remains off;
- Easy Auth files are unchanged;
- no deployment or traffic switch occurred.

- [ ] **Step 9: Commit**

```powershell
git add web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs web/tests/finops-portal-acceptance.spec.mjs web/tests/finops-decision-cache.spec.mjs docs/finops-portal.md docs/validation/2026-07-31-operations-roi-risk-decision-candidate.md
git commit -m "test(finops): validate ROI risk decision experience"
```

---

## Final Review Gate

Before any candidate deployment:

1. Request code review against `docs/superpowers/specs/2026-07-31-operations-roi-risk-decision-design.md`.
2. Confirm every task commit is present and `git diff --check` is clean.
3. Re-run full Python, Node, Vite, and Playwright suites.
4. Capture authenticated desktop and mobile evidence for ROI, risk, remediation, stale fallback, and no-repeat tab navigation.
5. Verify SQL migration twice against the candidate database.
6. Verify Redis hit, stale, revalidated, and unavailable fallback paths.
7. Keep backend and web candidate at zero production traffic.
8. Obtain explicit user approval before any production traffic switch.
