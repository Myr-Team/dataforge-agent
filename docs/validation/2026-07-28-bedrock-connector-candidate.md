# Bedrock connector candidate acceptance record

## Scope and safety boundary

This record covers the combined AWS Bedrock connector and member-budget change
set only after it is assembled into a single candidate revision with **zero
production traffic**. It is not a production-release approval.

The connector is configuration-only while these required settings remain in
effect:

```text
DF_PROVIDER_CONNECTORS_ENABLED=1
DF_AWS_BEDROCK_CONNECTOR_ENABLED=1
DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0
DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0
DF_FINOPS_ACTIONS_ENABLED=0
```

Credentials may be entered only through the authenticated write-only settings
UI. Do not put credentials in this document, terminal history, screenshots,
logs, issue comments, or source control. This record must not contain account
or subscription identifiers, ARNs, raw provider request identifiers, response
bodies, or secret values.

## Automated regression gate

| Check | Result | Evidence boundary |
| --- | --- | --- |
| `python -m pytest -q` | PASS — 1370 passed, 1 skipped | Completed locally; one existing experimental MAF warning remained. |
| `node --test` in `web` | PASS — 142 passed | Completed locally. |
| `npm run build` in `web` | PASS — 1778 modules transformed | Completed locally; the existing chunk-size advisory remained. |
| `npx playwright test` in `web` | PASS — 15 passed | Completed locally, including the mobile safe-conflict capture. |
| `git diff --check` | PASS | Completed after the final documentation and evidence changes. |

The mobile mock-regression image is local automated evidence only. It is not
AWS connectivity, Key Vault, candidate-revision, or production evidence.

## Unified zero-traffic candidate acceptance

All items below are **PENDING**. No unified zero-traffic candidate has been
created or exercised, and no real AWS test credentials have been provided.

| Acceptance item | Status | Evidence to add only after execution |
| --- | --- | --- |
| Candidate revision is deployed with zero production traffic | PENDING | Timestamp, candidate revision name, image digest, traffic allocation, and previous healthy backend/web rollback revision names. |
| Authenticated administrator saves one Bedrock connection | PENDING | Timestamp, safe UI screenshot path, and sanitized HTTP result category only. |
| Key Vault receives a new secret version | PENDING | Timestamp and secret-version existence confirmation only; never the value or version identifier. |
| One `ListFoundationModels` call succeeds | PENDING | Timestamp and sanitized success category only; never response body, account data, ARN, or raw request identifier. |
| Invalid credentials, denied permission, and unsupported region fail safely | PENDING | Timestamp and safe error category for each case only. |
| A second authenticated session refreshes and signs in | PENDING | Timestamp and confirmation that configuration persists while credentials remain absent. |
| Agent model routing excludes Bedrock models | PENDING | Timestamp and sanitized routing observation. |
| Critical backend log signals and secret-like values are zero | PENDING | Timestamp, bounded log-query window, and zero-count result only. |

## Missing prerequisites

1. A single build containing both the Bedrock connector and member-budget work
   must be available as a candidate revision with zero production traffic.
2. An authorized test AWS principal and its temporary test credentials must be
   available to the operator through the authenticated write-only UI; do not
   transmit them in chat, this file, or a runbook.
3. The candidate deployment identity must have the approved Key Vault secret
   permissions, and the authorized operator must be able to inspect version
   existence without reading secret values.
4. Authorized candidate observability access is required for bounded,
   redaction-safe log queries and for recording the allowed metadata above.
5. A separately authenticated second browser/device session is required for
   the credential non-return check.

## Safe execution sequence once prerequisites exist

1. Verify the five flags above and confirm the candidate revision has zero
   production traffic. Record the prior healthy backend and web revisions as
   rollback targets before any validation action.
2. In the authenticated settings UI, enter one temporary test credential set,
   save it, and run the connection test. Capture only a redacted UI image.
3. Confirm a new Key Vault secret version exists without reading or printing
   its value. Run one `ListFoundationModels` test and record only the safe
   outcome category.
4. Exercise invalid credentials, denied permission, and unsupported-region
   cases; record only their allowlisted error categories.
5. In a separate authenticated session, refresh the settings page and confirm
   the connection configuration persists while no credential fields are
   repopulated. Confirm Agent routing has no Bedrock model.
6. Query the approved candidate log window for critical signals and
   secret-like values; retain only the zero/non-zero counts and time window.
7. Stop at the release gate. Do not change traffic, enable external routing or
   APIM provisioning, or enable FinOps actions. Request explicit approval for
   any later traffic change.

## Candidate decision

**PENDING — NOT ACCEPTED.** Automated local regression may demonstrate
software behavior, but it cannot substitute for the missing zero-traffic live
candidate, AWS, Key Vault, second-session, routing, and log acceptance
evidence.
