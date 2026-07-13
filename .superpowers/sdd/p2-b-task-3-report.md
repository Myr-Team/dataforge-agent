# P2-B Task 3: Truthful Foundry ROI Adapter

## Official Capability Boundary

As of 2026-07-13, this task did not identify a stable public Azure AI Foundry REST endpoint for reading native ROI. The adapter therefore does not construct or request an inferred URL. It exposes only an injected `FoundryRoiProvider` protocol and a null default path.

`FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_AGENT_ID` are the only Foundry target configuration inputs. They must form a canonical Foundry project endpoint and agent ID, but they are never returned in API data. The later external-attestation remediation additionally permits only the non-secret pinned public verification key `DF_FOUNDRY_ROI_ATTESTATION_PUBLIC_KEY`; no signing or private key is configured by the application. No token, provider exception body, or raw provider response is stored or exposed.

## State Samples

| Situation | State | Configured | Meaning |
| --- | --- | --- | --- |
| No canonical endpoint or agent ID | `not_configured` | `false` | Native ROI cannot be discovered. |
| `DF_FOUNDRY_ROI_ENABLED=1` alone | `not_configured` | `false` | A feature flag is not evidence of a Foundry ROI surface. |
| Canonical target set but no provider injected | `not_configured` | `false` | No network request is sent. |
| Provider discovery or read fails | `unavailable` | `false` | Local ROI remains available; failure details are sanitized. |
| Provider discovers the target agent and ROI surface, external attestation has a valid pinned-key signature bound to the returned snapshot, then returns a valid snapshot | `connected` | `true` | Provider snapshot is exposed separately with source, observed time, and provider version. |

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

## Second Critical Remediation: External Asymmetric Attestation (2026-07-14)

This section supersedes the 2026-07-13 HMAC/capability design above. That earlier design is retained only as review history: a same-process HMAC secret and an adapter-private capability do not create a trust boundary against code executing in that process.

- The adapter now accepts a canonical `SignedFoundryRoiAttestation` envelope containing the attestation, SHA-256 public-key ID, and base64 Ed25519 signature. It verifies the signature over a versioned canonical JSON payload using only the deployment-pinned `DF_FOUNDRY_ROI_ATTESTATION_PUBLIC_KEY` public key. No signing or private key is present in application configuration, module state, or the adapter public API.
- An injected provider or attestation source is not trusted by identity, protocol conformance, echoed fields, or process placement. It can establish `connected` only by returning an envelope signed by the private key corresponding to the independently pinned public key. A wrong-signing/colluding source remains `unavailable`; a provider proof with no external envelope or pinned public key remains `configured_unverified`; no provider remains `not_configured`.
- Python code in the same process is not treated as sandboxed. The authentication boundary is the external holder of the Ed25519 private signing key plus deployment integrity for the pinned public key. The public surface does not expose a signing helper or private/capability material.
- `VerifiedProviderRead`, `_issued_by_adapter`, integrity tags, `reconcile_roi`, and `reconcile_foundry_read` have been removed. The only public reconciliation entry point is `reconcile_foundry_roi(local, provider, verifier)`, which validates the local window, performs target discovery and signature verification, reads and validates the provider snapshot, then invokes private reconciliation in one adapter-owned call. Public read results cannot be submitted to any reconciliation API.
- `backend/control_plane.py` now invokes the single orchestration entry point. `backend/dependency_health.py` remains discovery-only and therefore never receives a read or reconciliation object. `cryptography==43.0.3` is explicitly pinned in `backend/requirements.txt` for Ed25519 verification.

### Second Critical Remediation Test Evidence

Executed: `python -m pytest tests/test_foundry_roi.py tests/test_roi_service.py tests/test_governance_roi_summary.py -q`

Output: `51 passed in 4.66s`

Coverage: ephemeral Ed25519 keypairs; valid separately signed attestation; provider-as-verifier signed by a non-pinned key; colluding verifier signed by a non-pinned key; proof without a signed attestation; target/window/snapshot binding; removed wrapper/two-step public API; and retained local lineage, exact-set, sanitization, and authorization guards.

Executed: `python -m pytest -q`

Output: `499 passed, 1 warning in 46.69s` (the existing experimental workflow warning in `backend/maf_team_runtime.py`).

Executed: `python -m compileall backend tests`; `python -c "from backend.foundry_roi import SignedFoundryRoiAttestation, discover_foundry_roi, reconcile_foundry_roi; import backend.control_plane; import backend.dependency_health; print(discover_foundry_roi().state)"`; and `git diff --check`.

Output: all commands exited `0`; the import check printed `not_configured` with no configured Foundry target/provider.

## Third Critical Remediation: Request-Captured Config And Snapshot-Bound Attestation (2026-07-14)

This section supersedes the discovery-only connection behavior described by the prior asymmetric-attestation remediation. A valid discovery signature alone is no longer evidence that any provider ROI value is authentic.

- Each public discovery/read/reconciliation request first constructs a frozen internal adapter config containing the canonical target and parsed pinned Ed25519 public key. Environment-backed endpoint, agent ID, and public key inputs are read only during that construction, before any provider or verifier call. Later verification receives the captured public key, never an environment lookup. Provider and verifier calls receive isolated copies of the frozen target, so mutation of a passed object cannot alter the request config.
- The external verifier now receives the candidate sanitized `ProviderRoiSnapshot` after provider discovery and read. The versioned Ed25519 envelope signs both discovery evidence and `snapshot_digest`, a SHA-256 hash of canonical JSON for the complete snapshot: source, target fingerprint, normalized window, observation time, provider version, provider status, amount/currency/unit, and exact mapped run/outcome lineage.
- `connected` and reconciliation are emitted only when the pinned-key signature is valid, discovery fields match, the snapshot version matches, and the signed digest exactly equals the candidate snapshot digest. Any signed-then-mutated amount, lineage, window, or other material snapshot field is `unavailable`; no provider snapshot or difference is emitted.
- A valid envelope with no snapshot digest is `discovery_verified`, not `connected`. It exposes no provider ROI values and produces no difference. Dependency health already treats only `connected` as `ok`, while retaining the explicit discovery-only state for truthful diagnosis.
- The adapter still does not claim that arbitrary Python running in-process is sandboxed. The boundary is the captured deployment configuration plus the external Ed25519 private key; the closed public `reconcile_foundry_roi` flow remains the only reconciliation entry point.

### Third Critical Remediation Test Evidence

Executed: `python -m pytest tests/test_foundry_roi.py tests/test_roi_service.py tests/test_governance_roi_summary.py -q`

Output: `58 passed in 4.43s`

Coverage: request-start public-key/target capture despite malicious environment mutation; target-copy isolation; normal snapshot-bound Ed25519 attestation; discovery-only health/read/reconciliation state; and signature-after-sign tampering of amount, lineage, window, and another snapshot field. Existing target, sanitization, exact lineage, incomplete-lineage, local-authority, and closed-public-API tests remain included.

Executed: `python -m pytest -q`

Output: `506 passed, 1 warning in 46.98s` (the existing experimental workflow warning in `backend/maf_team_runtime.py`).

Executed: `python -m compileall backend tests`; `python -c "from backend.foundry_roi import SignedFoundryRoiAttestation, discover_foundry_roi, reconcile_foundry_roi; import backend.control_plane; import backend.dependency_health; print(discover_foundry_roi().state)"`; and `git diff --check`.

Output: all commands exited `0`; the import check printed `not_configured`.
