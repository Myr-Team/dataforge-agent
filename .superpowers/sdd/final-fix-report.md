# MAF Final Fix Report

Date: 2026-07-12
Base reviewed: `553b695`

## RED Inherited State

- The inherited working tree was preserved and reviewed file by file; no prior-worker changes were reverted.
- The controller's focused baseline reproduced as `63 passed, 1 warning`.
- Review findings were largely implemented, but the authoritative corpus, evidence catalog, and rubric still crossed the runtime boundary as raw dictionaries.
- Corpus validity required non-empty content and evidence, but did not require the evidence ref to resolve to a retrieved hit. Structurally malformed corpus data could fail request validation before the MAF fail-close result.
- Pre-audit guardrails returned metadata-bearing dictionaries without a typed guarded-report contract, and post-audit guardrail output was not revalidated.

## Changes

- Added typed authoritative contracts for corpus hits, corpus payloads, evidence, rubric scale/dimensions, and rubric version matching.
- Required workspace evidence now needs a non-empty, identified retrieval hit plus a non-empty corpus quote whose ref resolves to that hit. Synthetic `unknown#chunk`, unrelated refs, empty data, and malformed authoritative input all fail closed before any agent invocation.
- Added a typed `GuardedFeasibilityReport`; both pre-audit and post-audit guardrail outputs must validate against it. Feasibility and audit agents retain one bounded contract-correction retry.
- Preserved the inherited authoritative retrieval, evidence verification, rubric scoring, pre/post-audit guardrails, max-two-revision bound, producer delivery, live event sink, real participant spans, trusted telemetry projection, null unknown metrics, wall-time/agent-work separation, artifact allowlist, image import smoke, and provider error classification changes.
- Deterministic evaluation now supplies the canonical typed rubric as well as authoritative fixture evidence.
- Updated canonical design, implementation plan, and English/Chinese runtime documentation for typed evidence/rubric/guardrails and the canary hold.

## Verification

- `python -m pytest tests/test_maf_team_runtime.py tests/test_maf_integration.py tests/test_tracing_telemetry.py tests/test_backend_image_import_smoke.py tests/test_maf_evaluation_contract.py -q`
  - `68 passed, 1 warning in 8.63s`
- `python -m pytest -q`
  - `156 passed, 1 warning in 9.93s`
- `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`
  - Exit `0`; selection accuracy `1.0`, groundedness `0.916667`, unsupported-claim rate `0.083333`, task completion `1.0`, fallback rate `0.142857`; tokens remain `null`/`unknown`.
- `node --test src/mafViewModel.test.mjs` from `web`
  - `5 passed, 0 failed`
- `npm run build` from `web`
  - Exit `0`; Vite built `1751` modules.
- Staged Docker-context import/start smoke
  - Copied only `backend`, `ingest`, `workspaces`, `agents/build_agents.py`, `agents/tool_schemas.json`, `agents/prompts`, and `agents/rubrics` into a clean temporary root.
  - `python -m backend.import_smoke` exited `0`.
  - Verified `agents.build_agents`, `backend.maf_agents`, `backend.orchestrator`, and `backend.app` all resolved inside the staged root.
  - Started `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765`; `/api/health` returned `ok=true`, `service=dataforge-backend`.
- `git diff --check`
  - Exit `0`.

## Concerns

- Docker is not installed in this environment, so a real `docker build` and container launch could not be run. The Docker-context simulation and production Uvicorn entrypoint smoke passed, and the Dockerfile runs `python -m backend.import_smoke` during a real image build.
- The only test warning is Agent Framework's expected `FUNCTIONAL_WORKFLOWS` experimental API warning.
- The staged health response correctly reported unconfigured local Foundry, Search, Speech, Blob, and Content Safety dependencies; startup and module loading did not depend on them.
- `DF_MAF_TRAFFIC_PERCENT` remains defaulted to `0`; no production canary was enabled.
