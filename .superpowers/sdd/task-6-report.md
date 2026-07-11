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

Deterministic harness result from this checkout:

The report scope is `measurement_scope='deterministic_harness'` with `production_quality_claim=false`. Groundedness and unsupported-claim rate are fixture/reference-propagation contract checks, not production answer quality measurements.

| Metric | Value | Status | Sample size |
|---|---:|---|---:|
| selection_accuracy | 1.0 | measured | 7 |
| groundedness (fixture contract check) | 1.0 | measured | 7 claims |
| unsupported_claim_rate (fixture contract check) | 0.0 | measured | 7 claims |
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

## Stable MAF Compatibility Fix (2026-07-12)

Stable MAF collaboration now validates sequential participants against the runtime-checkable `SupportsAgentRun` protocol. The Task 6 deterministic fake previously exposed only `id`, `name`, and an async `run`, so `SequentialBuilder` rejected concurrent-case participants before execution.

The deterministic fake now matches the stable Task 3 test-double contract: it exposes `description`, stream-aware `run`, `AgentResponse`/`ResponseStream` results, and `create_session`/`get_session`, while retaining queued local outputs and injected failures with no network calls. Regression coverage verifies both direct protocol acceptance by `SequentialBuilder` and execution of every configured case.

Exact verification results:

- `python -m pytest tests/test_maf_evaluation_contract.py -q`: 7 passed, 1 upstream MAF experimental-workflow warning, in 3.63 seconds.
- `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`: completed all 7 cases. Deterministic harness metrics were `selection_accuracy=1.0`, `groundedness=1.0`, `unsupported_claim_rate=0.0`, `latency_ms=37.236957`, `tokens=null/unknown`, `task_completion=1.0`, and `fallback_rate=0.142857`. The groundedness values were fixture contract checks rather than production quality measurements, and the forced failure retained `fallback_attempts=1`.
- `python -m pytest -q`: 135 passed, 1 upstream MAF experimental-workflow warning, in 7.98 seconds.
- `npm run build` in `web`: passed; Vite 8.0.16 transformed 1,751 modules and built in 488 ms.
- No deployment or canary environment changes were performed.

## P1 Evaluation Truthfulness Fix (2026-07-12)

The deterministic JSON now declares `measurement_scope='deterministic_harness'` and `production_quality_claim=false` at the report level and on every metric. `groundedness` and `unsupported_claim_rate` additionally declare `interpretation='fixture_reference_propagation_contract_check'`: they are fixture/reference-propagation contract checks, not production answer quality measurements. Tokens remain `null`/`unknown` because the harness has no usage telemetry.

Exact verification results:

- `python -m pytest tests/test_maf_evaluation_contract.py -q`: 7 passed, 1 upstream MAF experimental-workflow warning, in 3.51 seconds.
- `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`: completed all 7 cases with `selection_accuracy=1.0`, fixture-contract `groundedness=1.0`, fixture-contract `unsupported_claim_rate=0.0`, `latency_ms=37.640143`, `tokens=null/unknown`, `task_completion=1.0`, and `fallback_rate=0.142857`; forced fallback remained exactly once with `fallback_attempts=1`.
- No deployment or canary environment changes were performed.
