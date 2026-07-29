## Task 5 — Bedrock documentation, full regression, and candidate boundary

### Delivered scope

- Documented the configuration-only Bedrock boundary in `README.md`.
- Added `docs/validation/2026-07-28-bedrock-connector-candidate.md` with the
  automated gate, explicit non-goals, exact pending live acceptance items,
  prerequisites, and the later safe execution sequence.
- Corrected the mobile Bedrock conflict evidence capture in
  `web/tests/finops-pricing-routing-remediation.spec.mjs`.

### Conflict screenshot correction

Root cause: the prior test captured
`output/playwright/bedrock-provider-mobile-conflict.png` before it submitted
the mocked `409` request. Therefore the image could not show the conflict
alert even though the test asserted it afterwards.

The corrected test now:

1. enters inert retry markers and submits the mocked `409`;
2. asserts the allowlisted Chinese conflict alert and asserts that the hostile
   response detail is absent;
3. asserts that the application retains the draft values after the conflict;
4. scrolls the visible alert into the mobile dialog viewport and captures the
   image only then; and
5. masks Access Key ID, Secret Access Key, and optional Session Token input
   regions white in the image.

Targeted verification:

```text
npx playwright test tests/finops-pricing-routing-remediation.spec.mjs --grep "Bedrock provider layout remains usable on mobile"
1 passed
```

Visual inspection of
`output/playwright/bedrock-provider-mobile-conflict.png`: the mobile dialog
shows the AWS Bedrock form, blank white-masked credential input regions, and
the visible safe Chinese conflict alert. It does not show the mocked hostile
detail or credential marker text.

### Full automated gate

| Command | Result |
| --- | --- |
| `python -m pytest -q` | PASS — `1370 passed, 1 skipped, 1 warning in 147.98s`; the warning is the existing MAF experimental warning. |
| `node --test` (in `web`) | PASS — `142` passed, `0` failed. |
| `npm run build` (in `web`) | PASS — Vite `8.0.16`, `1778` modules transformed. The existing chunk-size advisory remained. |
| `npx playwright test` (in `web`) | PASS — `15` passed. |
| `git diff --check` | PASS — completed after final report/document edits. |

### Candidate acceptance boundary

Status: **DONE_WITH_CONCERNS**.

The automated gate covers local software behavior only. The following remain
**PENDING / NOT RUN** and are not accepted:

- unified zero-traffic candidate revision and recorded rollback targets;
- real authenticated Bedrock connection save;
- Key Vault secret-version existence check;
- real `ListFoundationModels` success;
- bad credential, denied permission, and unsupported-region live checks;
- second authenticated browser/device-session refresh;
- candidate Agent-routing exclusion observation; and
- candidate bounded log query for critical signals and secret-like values.

No deployment, traffic change, external runtime routing, APIM provisioning,
FinOps action enablement, or external-resource creation was performed. Required
flags remain `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0`,
`DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0`, and
`DF_FINOPS_ACTIONS_ENABLED=0`.
