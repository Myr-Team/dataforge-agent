# P2-B Task 7: Azure Governance Integration Gate

## Scope

- Added `eval/run_p2_b_acceptance.py`.
- Added `tests/test_p2_b_acceptance_contract.py`.
- Documented the P2-B command and evidence boundary in both READMEs.

## Report contract

The evaluator emits `p2_b_azure_governance_acceptance` with eight gates:

1. `trace_configuration`
2. `trace_delivery`
3. `local_roi_state`
4. `foundry_roi_state`
5. `chargeback_lineage`
6. `invitation_claim_matching`
7. `audit_redaction`
8. `authorization`

Each gate records its state, evidence kind, sample count, local-contract result,
production-claim eligibility, failed reasons, and input lineage. The default
evaluator is deterministic and offline. It validates local contract behavior,
but records Azure Monitor delivery as `unmeasured` and Foundry native ROI as
`not_configured`; therefore its top-level `production_claim_allowed` is false.

## R2 evidence hardening

The evaluator now executes `backend.azure_monitor_client.build_trace_status`
for its trace-delivery fixture instead of treating an unknown remote result as
a checked result. A missing remote proof and a parsed but mismatched proof are
both explicitly recorded as `unmeasured`; neither is a delivery observation.

Observed provider payloads are always untrusted in this offline evaluator.
They may describe the required immutable binding, but no caller-supplied
source, timestamp, ID, digest, or self-declared attestation can promote a
gate. Azure Monitor evidence must be independently verified against the
expected workspace/run/correlation/build/revision and observation time by an
Azure Monitor query verifier. Foundry ROI requires the analogous Foundry
provider verifier attestation. This local command has no provider trust root,
so it records supplied payloads as `missing`, `invalid`, or `unverified` and
keeps `production_claim_allowed` false.

## R2 verification evidence

```text
python -m pytest tests/test_p2_b_acceptance_contract.py -q
9 passed in 2.31s

python eval/run_p2_b_acceptance.py --output generated-outputs/p2-b-acceptance-r2.json
passed: true
unmeasured_gates: [trace_delivery, foundry_roi_state]
production_claim_allowed: false

python -m pytest -q
702 passed, 1 upstream ExperimentalWarning, in 99.60s

python -m compileall -q backend tests eval
passed

cd web && npm run build
passed (Vite 8.0.16)
```

## Test-first evidence

The focused test was added before the evaluator module existed. Its first run
failed during collection because `eval.run_p2_b_acceptance` was absent. After
implementation:

```text
python -m pytest tests/test_p2_b_acceptance_contract.py -q
6 passed in 2.82s
```

```text
python eval/run_p2_b_acceptance.py --output generated-outputs/p2-b-acceptance.json
passed: true
unmeasured_gates: [trace_delivery, foundry_roi_state]
production_claim_allowed: false
```

The deterministic component checks execute the actual local ROI snapshot,
chargeback pseudonym projection, invitation claim consumption, audit HMAC
projection, and fail-closed sensitive authorization helper. They make no
network request and do not read or emit secrets.

## Regression evidence

```text
python -m compileall -q backend tests eval
passed

cd web && npm run build
passed (Vite 8.0.16; 1756 modules transformed)
```

The complete Python suite was also run:

```text
python -m pytest -q
5 failed, 694 passed, 1 warning in 106.08s
```

The five failures are integration blockers outside this task's permitted file
set. They were introduced by the preceding governance/privacy surface changes
or retain stale assertions for those changes:

- `tests/test_azure_monitor_status.py::test_trace_status_endpoint_authorizes_before_run_lookup_and_verifies_run_ownership`
  monkeypatches the former compatibility authorization helper, while the route
  now uses the new fail-closed sensitive authorization helper.
- `tests/test_roi_security.py::test_chargeback_excludes_untrusted_events_and_uses_workspace_scoped_hmac_and_currency_groups`
  still expects a raw `actor_id` in the public chargeback response, which
  conflicts with the new pseudonym-only governance contract.
- Three `tests/test_ui_truthfulness_contract.py` assertions still search for
  pre-governance UI source strings and need to be updated against the current
  view model rather than restored as stale literals.

At the time of the first full run, no code outside the Task 7 ownership list
was changed to conceal these failures. P2-B was not ready for release until
the cross-task regressions were resolved and the complete suite was green.

## Follow-up integration repair

The five failures were repaired without restoring any unsafe behavior:

- The Azure Monitor endpoint test now exercises the fail-closed sensitive
  authorization helper that the route actually uses.
- The chargeback privacy test now compares workspace-scoped `subject_label`
  values instead of asserting that the public API exposes `actor_id`.
- UI truthfulness tests now assert the current governance view model:
  separate local and Foundry ROI state, explicit observability fields, and the
  optional same-origin API base expression used by Vite.

Fresh verification after that repair:

```text
python -m pytest tests/test_azure_monitor_status.py tests/test_roi_security.py tests/test_ui_truthfulness_contract.py -q
32 passed in 3.96s

python -m pytest -q
699 passed, 1 warning in 103.50s

python -m compileall -q backend tests eval
passed

cd web && npm run build
passed (Vite 8.0.16; 1756 modules transformed)
```

The remaining warning is the upstream Microsoft Agent Framework
`ExperimentalWarning` for functional workflows; it is not a test failure.

## Remaining production evidence

- A trusted Azure Monitor verifier must retain a query result bound to the
  expected workspace/run/correlation/build/revision and observation time before
  trace delivery may be claimed.
- Foundry native ROI remains unavailable until an independently verified
  Foundry provider attestation with the same immutable binding is configured
  and observed.
- The immutable audit storage release gate still requires explicit Azure
  provisioning and its irreversible WORM-lock confirmation; this task does
  not provision or modify cloud resources.
