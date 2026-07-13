# P2-B Task 3: Truthful Foundry ROI Adapter

## Official Capability Boundary

As of 2026-07-13, this task did not identify a stable public Azure AI Foundry REST endpoint for reading native ROI. The adapter therefore does not construct or request an inferred URL. It exposes only an injected `FoundryRoiProvider` protocol and a null default path.

Only `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_AGENT_ID` are accepted as configuration inputs. They must form a canonical Foundry project endpoint and agent ID, but they are never returned in API data. No token, provider exception body, or raw provider response is stored or exposed.

## State Samples

| Situation | State | Configured | Meaning |
| --- | --- | --- | --- |
| No canonical endpoint or agent ID | `not_configured` | `false` | Native ROI cannot be discovered. |
| `DF_FOUNDRY_ROI_ENABLED=1` alone | `not_configured` | `false` | A feature flag is not evidence of a Foundry ROI surface. |
| Canonical target set but no provider injected | `not_configured` | `false` | No network request is sent. |
| Provider discovery or read fails | `unavailable` | `false` | Local ROI remains available; failure details are sanitized. |
| Provider discovers the target agent and ROI surface, then returns a valid snapshot | `connected` | `true` | Provider snapshot is exposed separately with source, observed time, and provider version. |

## Reconciliation Rules

- Local ROI stays authoritative and is never overwritten by provider values.
- Provider amounts must be finite, non-negative, and carry mapped run and outcome identifiers.
- A difference is emitted only when windows, currency, unit, and the complete run/outcome lineage sets exactly match. Otherwise `difference` is `null` with a reason.
- Provider `estimated`, `measured`, and `verified` status is retained separately. It never promotes the local ROI status.
- Dependency health exposes `foundry_roi` separately from the existing Foundry model probe, so unavailable native ROI cannot be mistaken for a healthy connection.

## Review Remediation (2026-07-13)

- `FoundryRoiTarget` now accepts only `https://<account>.services.ai.azure.com/api/projects/<project|_project>` with no port, user info, query, fragment, or extra path. Its SHA-256 target fingerprint binds the canonical endpoint and strict agent ID.
- `FoundryRoiProvider.discover(target)` must return a `DiscoveryProof` containing the same target fingerprint, a surface ID/version, UTC observation time, and state. A mismatched proof is `unavailable`; no read is attempted.
- `FoundryRoiProvider.read(target, window)` returns a snapshot with the same fingerprint. A mismatched snapshot is discarded as `unavailable`.
- Local ROI now records safe `observed_run_ids` from actual windowed source runs. Reconciliation requires non-empty business value plus exact, non-empty equality of provider mapped run IDs and outcome IDs with local lineage. Intersection, omission, and extra IDs all produce `difference: null` and `reconciled: false`.
- A non-`connected` provider status unconditionally discards any attached snapshot before reconciliation.

## Second Review Remediation (2026-07-13)

- Provider discovery is now only a claim. `connected` requires a separate `FoundryRoiDiscoveryVerifier` to return a matching `VerifiedDiscoveryAttestation` for the target fingerprint, surface ID/version, UTC observation time, and observation source. With the production default verifier unset, a provider proof is `configured_unverified`, never `connected`.
- `read_foundry_roi` returns `FoundryRoiReadResult`; only an adapter-issued `VerifiedProviderRead` can reach public `reconcile_roi`. Raw provider snapshots, mappings, and self-constructed wrappers are rejected with `TypeError`.
- `RoiSnapshot` records `lineage_complete`, `truncated`, and `invalid_run_ids`. Invalid local run IDs are represented only by stable SHA-256 digests, never raw unsafe values. A read window beyond the record limit or any invalid run ID makes local lineage incomplete.
- Reconciliation returns `difference: null` and `local_lineage_incomplete` for truncated, invalid, incomplete, or empty local run/outcome lineage.

## Re-Review Trust Boundary Remediation (2026-07-13)

- A provider proof is now only an unsigned claim. `connected` additionally requires a `SignedFoundryRoiAttestation` whose HMAC verifies against `DF_FOUNDRY_ROI_TRUST_HMAC_KEY`, the adapter's pinned trusted-channel material. The verifier return object, protocol type, target fields, and object identity are not trusted by themselves.
- The adapter rejects `verifier is provider` even when the provider can produce a correctly keyed signature. A separate verifier with an unknown or colluding key fails signature verification. Without pinned trust material, including the normal production configuration, a provider proof remains `configured_unverified`; with no provider it remains `not_configured`.
- `VerifiedProviderRead`, provider snapshots, and discovery attestations are frozen. `read_foundry_roi` adds a process-private HMAC integrity tag over target fingerprint, attestation, and the full snapshot including its window. Public reconciliation recomputes this tag, so self-constructed wrappers, toggled private flags, and post-issuance replacement or mutation fail closed.

### Re-Review Test Evidence

Executed: `python -m pytest tests/test_foundry_roi.py tests/test_roi_service.py tests/test_governance_roi_summary.py -q`

Output: `53 passed in 4.35s`

Coverage added: provider alone, provider-as-verifier, colluding untrusted verifier, trusted independently signed verifier, self-constructed wrapper, toggled private attribute, snapshot replacement, and all prior target, sanitization, window, lineage, truncation, and invalid-ID guards.

Executed: `python -m pytest -q`

Output: `501 passed, 1 warning in 46.86s` (the remaining warning is the existing experimental workflow warning in `backend/maf_team_runtime.py`).

Executed: `python -m compileall backend tests` and `python -c "from backend.foundry_roi import HmacFoundryRoiAttestationSigner, SignedFoundryRoiAttestation, VerifiedProviderRead, discover_foundry_roi; import backend.control_plane; import backend.dependency_health; print(discover_foundry_roi().state)"`

Output: both commands exited `0`; the import check printed `not_configured` with no pinned trust material.
