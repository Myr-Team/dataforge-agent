# Blockers

## B4-ACA-Ingress-Timeout

- Status: bypassed, not blocking current cloud run.
- Scope: Batch4-S1 requested ACA ingress timeout confirmation/extension for long streams.
- Finding: `cae-dataforge-dev` currently has no Premium ingress configuration, and the Container App environment has no workload profiles configured. Azure Container Apps default HTTP ingress request timeout remains 240s in this environment; raising idle/request timeout requires Premium ingress on a workload-profile environment, which changes the environment/capacity model.
- Bypass: reduced `/api/chat` wall time and event gaps instead of changing ingress. Revision `ca-dataforge-backend--0000034` completed 5 true Chromium browser streams in 36.755s-42.744s with max chunk gap 8.115s, no network errors, no `event:error`, and exact `answer_delta == final.text` parity.
- Follow-up decision needed only if future reports are expected to exceed 240s again: migrate/create a workload-profile Container Apps environment and enable Premium ingress timeout explicitly.
