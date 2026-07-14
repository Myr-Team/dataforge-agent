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

## R3 Persistence Repair

- Normal MAF summaries intentionally persist an ID-only `evidence_bundle`; the selected full contract remains at `artifact.capability_packs`.
- Before recursive metadata sanitization, the run store rebuilds a nested MAF bundle only from that sibling, registry-projected artifact contract. The nested ID list is overwritten from the reconstructed records; it never accepts IDs from the nested historical payload itself.
- This preserves the normal selected IDs through local runs, Blob run documents, Blob registry summaries, and run-summary API output, while an unselected registered ID, invalid ID, email, or directive in nested metadata is dropped.
- The regression fixture uses the exact `EvidenceBundle.persisted_metadata()` shape (only `capability_pack_ids`), forces a Blob-backed detail read, and checks run detail, Blob document, registry summary, API summary, and list output.
- R3 verification: `85 passed, 1 warning` for the focused capability-pack/MAF/evidence/run-summary suite; `739 passed, 1 warning` for the full backend suite. The warning is the existing MAF experimental-workflow notice.

## R4 No-Source Metadata Repair

- Before the generic registry sanitizer runs, run-store recursively clears capability metadata that is not anchored to an artifact-level selected contract.
- A direct `artifact.maf.evidence_bundle` is rebuilt only when the sibling `artifact.capability_packs` projects to safe selected records. Without that contract, both nested IDs and records remain empty; the same rule applies to Blob registry MAF summaries without a safe top-level contract.
- The historical local/Blob/API regression fixture contains a registered ID plus email and directive text but no sibling contract. It now exposes zero capability metadata on every read path, while the R3 normal ID-only MAF fixture continues to persist its selected safe pack.
- R4 final verification: `85 passed, 1 warning` for the focused capability-pack/MAF/evidence/run-summary suite. The warning is the existing MAF experimental-workflow notice.

## R5 Signed Selection Provenance

- Every newly selected capability-pack contract now carries bounded server-produced provenance: a fixed source/version, deterministic fingerprints for the normalized selector inputs and safe record projection, audit-key identifier, and an HMAC signature binding the exact selected IDs and records.
- The signature is created only after the internal selector recomputes a contract from normalized goal, schema profile, and quality inputs. Caller text, names, reasons, and IDs do not influence the persisted provenance.
- Artifact, MAF persisted metadata, run summary, Blob registry row, raw run detail, and the `capability_pack_selection` trace event retain records only when the provenance validates against the exact safe projection. Missing, invalid, or mismatched provenance clears both records and legacy IDs; historical records without provenance fail closed.
- MAF participants continue to receive only registered role-scoped guidance. The provenance object is stripped before any participant payload is created.
- Regression coverage proves a valid signed internal selection survives local, Blob, API summary, and trace reads. It also proves that a registered-but-unselected `growth_retention` record carrying an email/directive, paired with a valid signature for a different selection, is cleared from artifact, run summary, raw log, and trace.
- R5 focused verification: `79 passed, 1 warning` via `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q`; `python -m compileall -q` for changed backend modules and `git diff --check` also passed. The warning is the existing MAF experimental-workflow notice.

## R6 Scope-Bound Provenance

- Provenance version `2` now signs the exact selected records together with the server-owned `workspace_id`, durable conversation scope (falling back to the run ID only when no conversation ID exists), and a fresh server-generated nonce.
- The persisted provenance contains only SHA-256 scope fingerprints plus the nonce and HMAC material. Validation receives the expected run scope, compares the derived workspace and scope fingerprints with `hmac.compare_digest`, then verifies the HMAC using the expected scope. Missing, invalid, or copied provenance clears records and legacy IDs.
- The orchestrator creates the scoped selection before MAF planning; MAF rebuilds the evidence bundle with the same scoped context; run persistence, nested MAF metadata, Blob run documents, Blob registry summaries, and selection trace events validate against the owning run scope. Derived artifact/plan versions retain the source conversation scope intentionally.
- Public run summaries return only `capability_pack_integrity` (`verified` or `unavailable`) and no signing material. Public trace details, run-log raw data, workspace run lists, and latest-analysis artifacts remove `capability_pack_provenance`; MAF participant payloads continue to exclude all selection metadata.
- Regression coverage creates one signed source run, then copies the same record/provenance to a different run in the same workspace and a run in another workspace. The source survives local and Blob persistence; both copies expose empty pack records. The test also verifies that public summary, trace, and raw log serializations contain no provenance, signature, or nonce.
- R6 focused verification: `80 passed, 1 warning` via `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q`; changed backend modules compiled successfully. A full `python -m pytest -q` invocation exceeded the 120-second command limit without emitting a failure, so it is intentionally not recorded as a passing full-suite result.

## R7 Public Artifact Projection

- Added one recursive `public_artifact_projection` boundary for client-facing artifacts. It validates the signed, scope-bound selected-pack contract, retains only safe registered pack records and bounded `capability_pack_integrity`, and removes provenance material including signatures, nonces, and fingerprints at every nesting level.
- `/api/workspaces/{workspace_id}/latest-analysis` now returns the public artifact projection. Its synthetic `final` trace event reuses that already-projected artifact, so the route cannot return an internal artifact through either response field.
- Every final SSE event now passes through the same projection in the real `_frame()` emitter. This covers normal MAF completion, MAF terminal fallback, legacy revision fallback, and normal legacy final paths without changing internal persistence or MAF participant inputs.
- R7 regression uses an actual `TestClient(app).get("/api/workspaces/workspace-1/latest-analysis")` request and decodes the JSON emitted by the actual `_frame("final", ...)` SSE function. It verifies valid pack IDs and `verified` integrity remain available in the route artifact, synthetic trace final artifact, and SSE artifact while a recursive structural assertion confirms no provenance object, signature, nonce, or scope/workspace fingerprint is exposed.
- R7 verification: `1 passed` for the direct route/SSE regression, then `81 passed, 1 warning` via `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q`. The warning is the existing MAF experimental-workflow notice.

## R8 Integrity-State Forgery Repair

- The public projection now removes every incoming `capability_pack_integrity` field recursively, including standalone nested historical metadata with no pack contract.
- It recreates the bounded `verified` state only where the same map carries a valid signed, scope-bound selected-pack contract. Maps without a valid contract cannot surface a caller-supplied or historical integrity state.
- Regression coverage sends a valid root selection plus forged standalone nested `verified` states through the actual `/api/workspaces/{workspace_id}/latest-analysis` route and actual `_frame("final", ...)` SSE encoder. The root selection remains verified; both nested forged values are absent or unavailable.
- R8 verification: direct regression `1 passed`; focused capability-pack and MAF suite `82 passed, 1 warning` via `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q`. The warning is the existing MAF experimental-workflow notice.

## R10 Public Trace/Detail Projection

- Added one control-plane recursive public-detail projection for trace data and raw run-log data. It strips all capability-contract records, IDs, integrity state, provenance, and signing fields at any depth before a stored or synthesized event reaches a client.
- `trace_from_run`, `_flow_trace_from_run`, and `run_log.raw` now use this projection. The synthetic final trace event is also projected, while the top-level latest-analysis artifact and run summary retain their separately validated, bounded capability-pack display.
- Regression writes a persisted arbitrary `tool_result` step with forged `verified` integrity, IDs, records, signature, nonce, and every scope/selection fingerprint. It validates the actual `/api/workspaces/{workspace_id}/latest-analysis` response (`trace` and `run_trace`) and run-log response contain none of those fields, while the signed top-level artifact and run summary remain verified.
- R10 verification: direct persisted-step regression `1 passed`; focused capability-pack and MAF suite `83 passed, 1 warning` via `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q`. The warning is the existing MAF experimental-workflow notice.

## R11 Depth-Boundary Safety

- Both control-plane public detail projection and source-side run-step compaction now return the fixed `[truncated]` marker at their depth boundary. They never stringify an original nested map or list after the boundary, so hidden metadata cannot reappear inside a compacted string.
- Public artifact projection now removes incoming pack records, legacy IDs, and integrity state before recursion, then recreates all three only from a valid signed, scope-bound contract. Invalid or standalone nested metadata is omitted rather than represented as an `unavailable` contract.
- Regression creates eight nested maps with the deepest map containing every capability-contract key, forged `verified` state, and unique signing values. It validates actual latest-analysis `trace` and `run_trace`, the run trace and log endpoints, run-log raw data, and decoded `_frame("final", ...)` SSE projection. Their serialized protected surfaces contain none of the forged keys or values, while the valid root artifact remains verified.
- R11 verification: direct deep-boundary regression `1 passed`; focused capability-pack and MAF suite `84 passed, 1 warning` via `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q`. The warning is the existing MAF experimental-workflow notice.
