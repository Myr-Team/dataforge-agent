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
