# External Provider Candidate Validation — 2026-07-28

## Scope

This candidate exposes the settings entry points for:

- DeepSeek provider connection management.
- Entra group-to-workspace access governance.
- Provider-aware Agent model selection and FinOps attribution contracts.
- Redis reuse evidence separated from provider-side cache evidence.

It does **not** enable live external-provider routing, automatic APIM provisioning,
or FinOps production actions. No provider key is stored in this document or in the
repository.

## Code and regression evidence

- Candidate HEAD: `bc697c0`
- Python: `1340 passed, 1 skipped`
- Node: `138 passed`
- Vite: build succeeded (`1777` modules)
- Playwright: `12 passed`
- `git diff --check`: clean

Visual review artifacts:

- `output/playwright/model-governance-agent-desktop.png`
- `output/playwright/model-provider-settings-desktop.png`
- `output/playwright/identity-governance-desktop.png`
- `output/playwright/model-provider-settings-mobile.png`

The screenshot directory is validation output and is not committed.

## Immutable images

| Component | Tag | Digest |
| --- | --- | --- |
| Backend | `dataforge-backend:external-provider-bc697c0` | `sha256:7a7f453461e61d57477a8ad22a648ba91a5b5eb03e6d93a66d41b74a8e460e37` |
| Web | `dataforge-web:external-provider-bc697c0` | `sha256:31ce164107a29f5ba8e56d8f15132127d0d89cd22d0c255c9ccfd6f416ffb4d0` |

## Candidate and production revisions

| Component | Revision | Health | Traffic |
| --- | --- | --- | --- |
| Backend | `ca-dataforge-backend--extbc697c0` | Healthy | 100% |
| Web | `ca-dataforge-web--extbc697c0` | Healthy | 100% |

Both revisions were created and accepted at 0% before release. After the user
approved direct configuration access, Backend was promoted first and Web second.
Backend and Web each have exactly one non-zero traffic entry at 100%.

The Web candidate proxies API requests to the Backend candidate revision-specific
endpoint.

Recorded rollback revisions:

- Backend: `ca-dataforge-backend--fui6c750`
- Web: `ca-dataforge-web--fui6c750`

## Candidate feature gates

| Setting | Candidate value |
| --- | --- |
| `DF_PROVIDER_CONNECTORS_ENABLED` | `1` |
| `DF_ENTRA_GROUP_GOVERNANCE_ENABLED` | `1` |
| `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED` | `0` |
| `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED` | `0` |
| `DF_FINOPS_ACTIONS_ENABLED` | `0` |

The existing Easy Auth configuration was not modified.

## Data and security gates

- The additive FinOps SQL migration completed twice before candidate creation.
- The model-provider, provider-route and Entra-mapping tables were verified.
- The temporary SQL firewall rule was removed after migration.
- The Backend managed identity has the existing scoped Key Vault secret access
  and audit-storage access required by the candidate.
- Provider keys remain write-only and are never returned by the API.
- Entra group identifiers are not returned after a mapping is saved.

## Live candidate acceptance

Unauthenticated checks:

- Backend `/api/health`: `200`
- Backend `/api/model-providers`: `401`
- Backend `/api/identity-governance`: `401`
- Web root: `401`

Authenticated checks using the current Azure identity and the existing Easy Auth
application audience:

- Web candidate `/api/model-providers`: `200`, valid `count/items` response.
- Web candidate `/api/identity-governance`: `200`, valid mappings, membership
  resolution and permissions response.
- Both endpoints returned real empty states; no sample data was generated.
- The production Web host loaded the DataForge application successfully after
  traffic promotion.
- The production DOM showed the complete primary navigation immediately,
  including Operations Management and Settings.

Audit persistence acceptance:

- Read the existing routing policy from an owned workspace through the candidate.
- Submitted an equivalent routing policy with its current `base_revision`.
- The write returned `200`, and the policy revision advanced from `0` to `1`.
- The workspace had no execution-kind or Agent assignments before the check and
  still had none after the check; no functional route assignment changed.
- Candidate logs after the write contained zero audit-persistence, Blob 403,
  Key Vault 403, traceback or unhandled-server-error signals.

This directly exercises the path that previously returned
`Audit persistence is required`.

## Remaining gates

- A real DeepSeek key has not been entered. The user should add it through the
  write-only settings field; it must not be sent in chat or committed.
- Live Agent routing to DeepSeek is intentionally unavailable while
  `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0`.
- Automatic APIM provisioning remains unavailable while
  `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0`.
- The runtime MAF external-provider fallback is not yet a completed acceptance
  path and must not be represented as production-ready.
- Revision-specific Easy Auth browser navigation did not complete its interactive
  redirect. Authenticated candidate API acceptance used the same existing
  application audience instead; local Playwright visual acceptance covers the
  candidate UI. The production host's normal Easy Auth redirect completed and
  loaded the signed-in portal.
- No APIM job, Easy Auth setting, or FinOps action flag was changed.

## Release decision

The provider and Entra configuration surfaces are open in production. Their
authenticated read/write foundations are verified, and production Backend/Web
traffic is on the accepted `extbc697c0` revisions.

This release intentionally stops at configuration management. Live DeepSeek Agent
routing remains blocked until:

1. A user enters a real provider key through the production settings UI and completes the
   connection test.
2. The provider is governed through the intended APIM path.
3. External runtime routing is fully wired and separately accepted.
4. A final, explicit production traffic approval is recorded.
