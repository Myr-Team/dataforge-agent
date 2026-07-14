# P2-C Task 2: Capability Pack Integration

## Scope

- Selected capability packs are derived before MAF collaboration planning from the normalized goal, mapped schema roles, metric families, and quality aggregates.
- Selection is persisted on the artifact and run contract as `capability_packs` with `pack_id`, `confidence`, `reasons`, `matched_schema_roles`, and `missing_evidence`.
- The conversation trace records a `capability_pack_selection` event with `source: normalized_goal_schema_profile_quality`.

## Agent Boundaries

- Agents do not receive raw pack selections in their request payload.
- The evidence bundle projects only selected pack guidance by agent role:
  - coordinator and corpus: questions
  - market and feasibility: questions and validation methods
  - auditor: validation methods
  - producer: artifact sections
- Guidance is explicitly marked `capability_guidance_is_observed_evidence: false`.
- Pack guidance contains no score, verdict, or conclusion fields. Runtime artifacts explicitly identify `verdict_source: evidence_guard`.

## Compatibility

- Existing `capability_pack_ids` bundle metadata remains unchanged for existing consumers.
- New `capability_packs` is additive in run summaries and structured result contracts.
- Unknown or invalid selections are excluded from the new contract.

## Verification

- Red phase: `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q` failed before the contract was implemented.
- Focused regression: `100 passed, 1 warning` for capability-pack, MAF, evidence bundle, generalization, and run-summary suites.
- Full backend regression: `732 passed, 1 warning` via `python -m pytest -q`.
- Frontend build: `npm run build` completed successfully.
- Static checks: `python -m compileall -q backend tests eval` and `git diff --check` completed successfully.

## Remaining Limitation

This task intentionally treats mapped time fields as insufficient proof of temporal coverage and does not infer entity relationships from raw data. P2-C onboarding/profile work can add explicit, user-confirmed semantic relationships and temporal coverage without weakening evidence authority.
