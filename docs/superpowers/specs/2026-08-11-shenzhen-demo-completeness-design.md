# Shenzhen Site-Selection Demo Completeness Design

## Goal

Provide one deterministic, internally reconcilable demo story in `demo-corpus`: a fictional regional retail company evaluates Shenzhen candidate stores through DataForge, and the same runs power FinOps, Trace, evidence, operations signals, and ROI demonstrations.

## Truth boundary

All generated records carry `provenance=synthetic_demo`, a fixed scenario and batch identifier, and safe fictional references. They never contain secrets, raw identity, full prompts, full provider bodies, or production responses. The normal demo route should be populated, but missing price/cache/evidence must remain unavailable rather than becoming zero.

Synthetic scenario, measured process evidence, and synthetic independent verification are separate:

- `scenario`: estimated process value and explicitly not realized ROI;
- `measured`: observed synthetic pilot process metrics awaiting business validation;
- `demo_verified`: an independently reviewed synthetic process result displayed as `演示验证结果 · 合成数据`, never as a production-quality customer ROI claim.

The deterministic L1 regression fixture must not emit a production-style verified ROI. Demo verification belongs only to the allowlisted demo workspace projection.

## Scenario

- Workspace: `demo-corpus`
- Scenario: `shenzhen-site-selection-v1`
- Fictional company: 云岭生活
- Window: 30 days ending at explicit anchor `2026-08-11T12:00:00Z`
- Site-selection tasks: 96
- Model/retrieval/tool request facts: 2,480
- Reports: 78
- Evidence-review tasks: 18
- Monthly AI operating cost target: USD 206.40, composed of model estimate, retrieval/tools, and local infrastructure allocation.

Agents remain Coordinator, Corpus Analyst, Market Researcher, Feasibility Analyst, Auditor, and Producer. Models include the configured Foundry and DeepSeek routes available in the repository; generated route evidence is synthetic/fixture, never observed provider evidence.

## Data architecture

Add a pure deterministic generator under `backend/finops/` and keep persistence in the existing allowlisted demo initializer. Inputs are the scenario manifest, corpus manifest digest, fixed seed, batch ID, and explicit anchor. Outputs include request events, safe run events/steps/model attempts, price mappings, scenario/measured/demo-verified outcomes, artifacts, expected operations signals, and a reconciliation manifest.

Generated request, run, trace, evidence, and outcome records share stable request/run/correlation/attempt references. Existing query services and anomaly rules produce UI aggregates and findings; the generator must not directly hardcode final risk findings.

## Operations signal contract

The generated facts must cause the existing scanner to produce differentiated evidence for failure rate, P95 latency, unpriced requests, governance-evidence coverage, token growth, provider-cache opportunity, and daily cost attention. Cross-signal reuse of one request is permitted only with a distinct signal reason and must never double-count totals.

## ROI contract

Scenario values describe site-selection workflow efficiency only: analysis duration, analyst hours, external research spend, rework, and audited report cost. Avoided bad leases or future store revenue remain scenario ranges and never become verified outcomes.

The presentation target is:

- scenario monthly benefit USD 6,240;
- monthly operating/allocated/amortized input USD 1,586.40;
- scenario ROI 293.3%;
- measured pilot: 18 paired site evaluations, 17.8h historical versus 8.1h assisted;
- demo verification: 174.6h synthetic reviewed process savings with distinct outcome and finance-review actors.

## Acceptance

- Re-running the same seed/batch/anchor produces identical canonical digest and IDs.
- All request, department, agent, model, token, cache, and cost totals reconcile.
- Every evidence reference exists and can resolve to a safe request and run Trace.
- Every seeded run has at least one safe step and model attempt.
- Price-bearing model attempts map to an exact official catalog key/revision; otherwise they are unpriced.
- Result cache and provider token cache remain separate.
- All four FinOps pages, request evidence, Trace, and operations AI have populated demo content without hiding real error states.
- The generator cannot write outside the allowlisted demo workspace and never deploys automatically.

