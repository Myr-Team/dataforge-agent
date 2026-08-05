# Production load reliability candidate acceptance

Date: 2026-08-04

Decision: **backend/API candidate PASS; auth-free test UI gate PASS; production promotion NOT APPROVED**.

## Scope

This candidate addresses the intermittent production state where the shell was
visible while operations remained on `正在核验运营管理权限` and multiple workspace
requests stayed pending. It does not change Easy Auth, Entra application
permissions, workspace role semantics, FinOps action gates, or external model
routing.

Implemented controls:

- direct workspace owners and persisted members are authorized before any Entra
  group-overage lookup;
- one request reuses its sanitized actor, concurrent group-overage lookups share
  one loader per backend process, observed membership is cached for 120 seconds,
  and an unavailable result is fail-closed and cached for 30 seconds;
- workspace access and governance capability reads time out after 8 seconds,
  dashboard reads after 15 seconds, and a dashboard timeout cannot fan out into
  the legacy five-request fallback;
- a matching verified workspace capability remains visible during refresh, while
  an initial timeout becomes one explicit `重新检查` state instead of an infinite
  spinner;
- `/dataforge-logo.png` has a 30-day public cache and seven-day
  stale-while-revalidate policy.

## Local automated gate

| Gate | Result |
| --- | --- |
| Python | `1708 passed, 1 skipped`, one existing MAF experimental warning |
| Node | `281 passed` |
| Vite | build succeeded; existing main-bundle split warning remains |
| Playwright | `53 passed` on a fresh isolated preview port |
| Operations-focused Playwright | `14 passed`, including the 8-second stalled-capability retry state and desktop/mobile layouts |
| Cache-first performance repeat | `5 passed`; browser-internal time remains below 300 ms and no second ROI decision request is sent |
| Whitespace | `git diff --check` clean before candidate build |

The performance acceptance was changed from controller-to-browser round-trip
timing to browser-internal timing. The assertion still enforces `<300 ms` and
now also requires that cached ROI navigation does not issue another ROI request.

## Immutable images

| Component | Image | Digest |
| --- | --- | --- |
| Backend | `acrdataforgedev.azurecr.io/dataforge-backend:loadrel-aaa81d19` | `sha256:d969c277bc6995e8a75ecc3ed5939fc813a289780bb6b1475f07518e9087f400` |
| Web | `acrdataforgedev.azurecr.io/dataforge-web:loadrel-f300a394` | `sha256:32a6489664b990bf6efb3754da3d60f4abc9ca93dd6f26512526a29f0ad36140` |

The root build context excludes `workspaces/ws-*`, test output, local virtual
environments, Git data, environment files, credentials, and private key formats.
The Web image was built from the bounded `web/` context.

## Zero-traffic Azure candidate

| Component | Stable revision | Candidate revision | Stable / candidate traffic | Candidate state |
| --- | --- | --- | --- | --- |
| Backend | `ca-dataforge-backend--gov098e2ca` | `ca-dataforge-backend--loadrel0819` | `100 / 0` | Healthy, Provisioned, one ready container, zero restarts |
| Web | `ca-dataforge-web--finui-a5ae457` | `ca-dataforge-web--loadrelf300` | `100 / 0` | Healthy, Provisioned, ready containers, zero restarts |

Both applications remained in Multiple revision mode with one and only one
positive traffic target. The Web candidate upstream is the Backend candidate
revision FQDN. `DF_FINOPS_ACTIONS_ENABLED=0`, `DF_FINOPS_READ_ENABLED=1`, and
both candidate templates retain `minReplicas=1`.

An earlier Web candidate built before the complete-response-body timeout review
was deactivated after `ca-dataforge-web--loadrelf300` reached Healthy. Its
temporary validation label was removed; it never received production traffic.

Backend candidate health checks returned HTTP 200 three times. The first check
was 4337 ms while the candidate initialized; the next two were 231 ms and 230
ms.

## Trusted candidate API acceptance

The checks below were sent directly to the Backend candidate with the existing
Container Apps secret and configured owner identity held only in process memory.
No token, proxy secret, tenant id, object id, email, or response body was written
to this record or command output.

| Endpoint | Status | Elapsed | Safe projection |
| --- | --- | --- | --- |
| `/api/workspaces/demo-corpus/access` | 200 | 755 ms | authenticated owner, allowed |
| `/api/workspaces/demo-corpus/governance/capabilities` | 200 | 236 ms | FinOps visible, six granted permissions |
| `/api/workspaces/demo-corpus/dashboard` | 200 | 2723 ms | expected workspace, health OK |

A bounded 200-line candidate log scan found zero critical/traceback markers,
zero Microsoft Graph failure markers, and zero secret-like output markers.

## Authentication boundary

The final candidate root returned 401, confirming that the candidate does not
bypass Easy Auth. Root, logo, and API requests on the preceding candidate with
the same app-level auth configuration also returned 401. An explicit login
route reached Entra, but Entra rejected the temporary revision-label callback
with `AADSTS50011` because that hostname is not registered as an application
redirect URI.

The accepted test environment is auth-free, so the temporary revision-label
callback is not a test-environment acceptance gate. Per the release constraints,
the candidate did not modify Easy Auth or the Entra application registration.
Therefore:

- auth-free desktop/mobile test behavior is accepted by the fresh Playwright gate;
- signed-in desktop/mobile candidate screenshots are **not claimed and not
  required for the test environment**;
- the live candidate logo response header is **not claimed** because the asset is
  protected by the same authentication boundary;
- desktop/mobile, chart, tooltip, retry-state, and logo-cache behavior are covered
  by the fresh local Playwright and Node gates above;
- production traffic remains unchanged; production-host authentication is
  rechecked immediately after an explicitly approved promotion and triggers
  rollback if it fails.

## Repository credential scan

The GitHub PR head and the local remote-tracking branch both resolved to
`e6e4fb400ca5fb85db5d306e9aa7a1af8a7cf833` before this documentation-only
update. Gitleaks 8.30.1 was downloaded from its official GitHub release and its
SHA-256 checksum was verified before use.

- the exact PR range contained zero findings;
- the target branch ancestry contained 16 generic-rule findings, all in tests or
  `.env.example`;
- the current tracked snapshot contained 15 generic-rule findings: eight
  deterministic test seeds, six opaque request/trace references, and one empty
  example setting;
- all current findings were classified, and none matched a known provider key,
  private-key, JWT, or cloud connection-string pattern;
- the only tracked environment file outside `.env.example` is
  `web/.env.development`; it contains one loopback API URL and no sensitive
  variable name or credential-shaped value;
- the public GitHub repository currently reports native Secret Scanning and Push
  Protection as disabled. No repository-security setting was changed during this
  acceptance.

These scanner results found no committed credential. They do not replace secret
rotation if a credential is ever independently suspected to have been exposed.

## Promotion gate and rollback

Current stop decision: retain both candidates at zero traffic.

If production promotion is later approved, switch Backend first, verify health
and the trusted owner capability path, then switch Web and run the signed-in
production-host desktop/mobile smoke. Roll back Web to
`ca-dataforge-web--finui-a5ae457` and Backend to
`ca-dataforge-backend--gov098e2ca` if the permission state, dashboard, or
operations surface fails to converge.
