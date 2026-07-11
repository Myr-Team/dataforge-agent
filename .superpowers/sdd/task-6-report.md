# Task 6 Report: MAF Runtime Evaluation Gate

## Delivered

- Added a connector-free deterministic evaluation runner that compares a legacy baseline with the real `MafTeamRuntime` entrypoint using bounded fake agents.
- Added seven schema- and evidence-driven cases covering `direct`, `concurrent_research`, `specialist_handoff`, and `bounded_review` plus weak evidence, missing optional market evidence, ambiguous follow-up, a high-impact conclusion, and forced runtime failure.
- Added contract tests for case coverage, semantic-only routing inputs, truthful unknown metrics, stable documentation, and exactly-once fallback.
- Updated both READMEs for `off`/`audit`/`full`, stable canary selection, exactly-once fallback, supported collaboration patterns, stable package versions, and the explicit exclusion of Magentic and Foundry Hosted Agents.

## Deterministic Evaluation

Command:

```text
python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json
```

Measured result from this checkout:

| Metric | Value | Status | Sample size |
|---|---:|---|---:|
| selection_accuracy | 1.0 | measured | 7 |
| groundedness | 1.0 | measured | 7 claims |
| unsupported_claim_rate | 0.0 | measured | 7 claims |
| latency_ms | 35.325371 | measured | 7 cases |
| tokens | null | unknown | 0 |
| task_completion | 1.0 | measured | 7 cases |
| fallback_rate | 0.142857 | measured | 7 cases |

The forced runtime failure recorded `fallback_attempts=1`. Token usage is absent from the deterministic runtime result contract and is therefore reported as `null`/`unknown`, not zero.

## Verification

- `python -m pytest tests/test_maf_evaluation_contract.py tests/test_maf_team_runtime.py tests/test_maf_contracts.py -q`: 31 passed, one upstream MAF experimental-workflow warning.
- `npm run build` in `web`: passed; 1,750 modules transformed.
- `python -m pytest -q`: 103 passed, 1 failed. The failure is outside Task 6 ownership in `tests/test_tracing_telemetry.py::test_agent_trace_emits_foundry_agent_identity_without_raw_actor_email`; the installed OpenTelemetry `Status` object representation does not contain the test's expected `OK` string.
- No deployment or canary environment changes were performed.
