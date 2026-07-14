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

## Security Remediation

### Root cause

The initial Task 2 contract validated caller-supplied `CapabilitySelection` objects and then retained their reasons, missing-evidence text, and legacy `id` or `name` values. That allowed untrusted free text to reach a bundle, run artifact, or trace event.

### Remediation

- Caller input now contributes only an exact `pack_id` that is present in the bundled registry. `id`, `name`, reasons, and missing-evidence text are ignored.
- The evidence bundle independently reruns `select_capability_packs` from the bounded normalized-goal, schema-profile, and quality context. A caller ID is retained only when that internal selection independently selected it.
- MAF participant payloads remove both raw pack selections and the temporary selection context. Agents receive only role-scoped registered guidance.
- Run artifact, run summary, trace, raw run-log, and artifact-version paths defensively project capability-pack records to bounded registry-derived values. Unknown IDs and free text are excluded.

### Security Verification

- Added adversarial coverage for an invalid `id` or `name` containing an email and a valid `pack_id` carrying prompt-injection and email text in reasons, gaps, and roles.
- The tests prove the text is absent from the evidence bundle, MAF participant input, MAF artifact, run summary, trace, and raw run log while the internally selected registered pack remains available.
- Follow-up verification: `79 passed, 1 warning` for Task 2 evidence-bundle, integration, and MAF tests; `735 passed, 1 warning` for the full backend suite. The warning is the existing MAF experimental-workflow notice.

## R2 Contract Tightening

- Legacy `capability_pack_ids` now derives from the recomputed `capability_packs` contract, not from the raw candidate list. A registered pack that the internal selector did not choose cannot reach MAF agent input, artifact output, or the persisted bundle metadata.
- Run metadata sanitization is recursive. It rebuilds nested `capability_packs` and `capability_pack_ids` together in artifacts, MAF evidence bundles, event data, final payloads, proposals, and historical records.
- Blob registry summaries are sanitized when read, and persisted run/summary paths sanitize before storage. Local run details, Blob-backed run details, run summaries, raw logs, and latest-analysis all use the same safe projection.
- Regression coverage includes a registered-but-unselected pack and a historical local/Blob fixture with nested email, name, and directive payloads. API-visible serializations contain neither the untrusted strings nor divergent legacy IDs.
