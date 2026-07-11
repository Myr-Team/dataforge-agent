# DataForge Release And Evolution Design

## Goal

Publish the accumulated P0/P1 product hardening as a traceable production release, while defining a separate path from the current demo-grade ROI, MAF graph, and version comparison toward measurable business value and genuine multi-agent collaboration.

## Current State

- The backend and frontend build successfully and the backend test suite passes.
- Production Container Apps are running images built after the latest GitHub commit, so production, GitHub, and the local worktree are not currently traceable to one source revision.
- Microsoft Agent Framework currently owns the conditional audit/revise topology, but its executors wrap existing functions. The specialists are not yet independent `Agent` instances with their own tools, state, handoffs, and telemetry.
- ROI is an explicitly labelled DataForge estimate derived from token usage and configurable time-value assumptions. It is not measured business value and is not connected to Foundry native ROI.
- Iteration versions are run snapshots. Plan and artifact generation produce version entries, but those entries do not prove that new customer evidence changed a hypothesis or decision.

## Release Scope

This release will:

1. Commit the accumulated P0/P1 backend and frontend changes already represented in the working product.
2. Remove remaining static or misleading status values found during the release audit.
3. Keep ROI visibly estimated and prevent non-analysis snapshots from inflating analysis value calculations.
4. Preserve real run duration, actor, token, dependency, artifact, and workspace evidence in customer-facing surfaces.
5. Exclude temporary smoke scripts, logs, downloaded production payloads, and generated build output.
6. Build immutable backend and frontend image tags from the committed source and deploy those exact tags to production.

This release will not:

- claim that Foundry native ROI is connected;
- claim that workspace RBAC is enforced;
- replace the current analysis path with a new multi-agent runtime without evaluation coverage;
- treat generated demo data as customer-observed evidence.

## Truthfulness Rules

- A missing operational value is rendered as unknown or not recorded, never as healthy or successful.
- Estimated ROI must expose its assumptions and source.
- Analysis counts include analysis and follow-up model runs, not artifact-only or plan-snapshot records.
- A generated artifact version is a delivery snapshot, not an evidence improvement.
- An observed metric is only accepted as observed when it has source lineage, observation time, and an actor or connector.
- Industry capability selection can use schema and business-goal signals, but conclusions and scores remain evidence-derived.

## ROI Evolution

### Phase R1: Measurable Local ROI

Add a business outcome event model:

- `task_started`, `task_completed`, `human_minutes_baseline`, `human_minutes_actual`;
- `business_metric_before`, `business_metric_after`, currency, unit, and attribution window;
- model, tool, infrastructure, and human review costs;
- actor, workspace, run, experiment, and artifact lineage.

ROI states become:

- `estimated`: assumptions only;
- `measured`: customer-provided baseline and observed outcome;
- `verified`: outcome approved by an authorized reviewer and linked to source data.

### Phase R2: Azure Observability Correlation

Emit OpenTelemetry spans and attributes for model calls, tool calls, retries, cache hits, agent handoffs, evaluations, costs, and outcome event IDs. Correlate them in Application Insights and expose drill-down links from DataForge.

### Phase R3: Foundry Native ROI Adapter

Treat Foundry native ROI as an optional provider adapter while the feature remains private preview. The DataForge outcome ledger remains the source of customer business inputs so the product can operate when the preview is unavailable and can reconcile Foundry values instead of blindly replacing local evidence.

## Genuine MAF Evolution

### Phase M1: Independent Agents

Create first-class MAF agents for coordination, workspace evidence analysis, market research, feasibility, audit, and answer/artifact composition. Each agent receives:

- a bounded instruction set;
- only the tools required for its role;
- typed input and output contracts;
- independent token, latency, retry, and error telemetry.

### Phase M2: Dynamic Collaboration

The coordinator selects a collaboration pattern from task evidence:

- direct response for simple workspace questions;
- concurrent research and data analysis when both are required;
- handoff from analyst to feasibility specialist;
- bounded audit/revision loop for high-impact conclusions;
- human approval for sensitive actions and verified ROI events.

The selected agents and route must be visible in trace data. The system must be able to show that an agent was not called when it was unnecessary.

### Phase M3: Evaluation Gate

Before becoming the default path, the multi-agent runtime must outperform the current path on agent selection accuracy, groundedness, unsupported-claim rate, latency, token cost, and task completion. A feature flag keeps the current path as fallback during rollout.

## Iteration Evolution

Replace run-snapshot versions with an experiment ledger:

1. A version records hypotheses, decision, evidence set, gaps, metrics, and artifact references.
2. A pilot defines target metrics, baseline, sample, time window, and stop criteria.
3. Imported customer data is linked by file version, connector, query/table, timestamp, and actor.
4. The next analysis produces an evidence delta: added, removed, contradicted, or strengthened evidence.
5. The decision delta explains why verdict, score, scope, price, target segment, or pilot design changed.
6. Artifacts render the current version and a concise change log rather than duplicating the previous document.

Generated or simulated values must carry `synthetic` provenance and cannot promote a verdict to a stronger evidence tier.

## Industry Generalization

Use capability packs instead of industry-specific conclusions. Initial packs are:

- growth and retention;
- productization and pricing;
- site and channel selection;
- operational efficiency;
- campaign and service design;
- risk, compliance, and data readiness.

The coordinator infers applicable packs from the stated business goal, schema roles, metric types, temporal coverage, and data quality. Packs define terminology, common metrics, validation methods, and artifact structures only. They do not define scores, winners, or recommended opportunities.

## Release Verification

- Run the complete backend test suite and Python compilation.
- Build the frontend production bundle.
- Run targeted local API contract smoke tests.
- Commit and push only intended source, tests, docs, and demo seed files.
- Build immutable ACR images from the committed worktree.
- Update backend first, verify health and API compatibility, then update the frontend.
- Verify all six production pages, a real conversation response, run persistence, artifact listing, and governance status.
