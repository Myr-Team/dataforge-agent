# DataForge — turn sleeping data into product opportunities

[简体中文](./README.zh-CN.md)

**DataForge is a multi-agent system that turns an enterprise's existing data into product opportunities** — and then quantifies whether each opportunity is actually feasible, grounded in evidence. Upload your business data; multiple specialist agents collaborate to discover non-obvious, high-value product directions, score their feasibility on a five-dimension rubric, and produce deliverables (a PDF proposal, a concept image, and an audio summary) in one click.

> Built for the Microsoft GCR *AI Agent Frontier Hackathon* — **Track B (Pro Code)**. Orchestration, the feasibility engine, and the guardrails are all written in code; nothing here is a low-code drag-and-drop automation.

---

## The problem

Enterprises sit on mountains of data — field signals, transactions, device logs, environmental context — but beyond their core business, **nobody can say what else that data could become.** Hiring a data-science team to explore it is slow and expensive, so most data just sleeps.

The naive thought that started this project:

> *"I have all this data but can't find any new opportunity in it — so let me throw it all in and just **quantify** what my data is actually worth."*

DataForge automates exactly that.

**Real case that inspired it:** a company built floor-level anti-loss hardware and had piles of on-site signal data. They only ever used it for positioning — until they realized those signals were really *location / foot-traffic intelligence*, and built a store **site-selection app** on top of them that took off. DataForge is built to surface that kind of unexpected-but-defensible second curve from data you already own.

---

## Agent Execution Path

```
Upload data → auto profiling + indexing
            → multi-agent analysis (retrieval → analysis → market)
            → audit ⇄ revise loop (Microsoft Agent Framework)
            → grounded feasibility verdict (+ citations)
            → one-click artifacts (PDF / concept image / audio)
            → backfill real pilot metrics → iterate toward a flagship plan
```

Everything is streamed to the UI in real time (SSE), so you watch the reasoning happen — not just the final answer.

## PPT / Agent Briefing

Use this section when another agent needs to understand the product quickly and create slides.

**One-line positioning:** DataForge is a Pro-Code multi-agent productization studio for enterprise data: it turns existing files, tables, and external data connections into evidence-grounded business opportunities, then audits, discusses, produces, and iterates the proposal.

**Primary demo story:** a floor-level anti-loss / IoT signal dataset is reframed as a location-intelligence asset. DataForge evaluates whether it can become a "pop-up store / small-store site-selection advisory" service, surfaces evidence and gaps, generates a project PDF plus concept image, and then iterates v1 → v2 after pilot metrics are backfilled.

**What to show in a 5-minute product video:**

1. Open the default workspace and show the left navigation: Workspace, Data, Runs, Conversation, Artifacts, Settings.
2. Upload or select a site-selection dataset; show automatic parsing, file assets, and field/quality status in the Data Workbench.
3. Run auto analysis; explain the visible multi-agent execution path and the auditor's ability to revise or downgrade conclusions.
4. Ask a follow-up question in Conversation; the agent should give a calibrated provisional recommendation, gaps, and a low-cost pilot instead of blocking on missing evidence.
5. Generate artifacts; the PDF / concept image / audio are persisted to the Artifacts page and also recorded as a version entry.
6. Return to Workspace; show plan iteration, v1/v2 comparison, backfilled metrics, and the convergence chart toward a flagship plan.
7. Optional: connect SQL Database / Blob Storage by connection string, preview external data, import it into the file library, and send it to analysis.

**Slide angles that matter:**

- "Not a chatbot": it has data ingestion, retrieval, scoring, audit, artifact generation, and versioned iteration.
- "Not self-deceiving": evidence strength controls verdicts; unsupported claims stay as gaps or validation tasks.
- "Enterprise extensibility": local uploads, SQL / Blob connection, Azure AI Search, Foundry web search, Blob persistence, Container Apps deployment.
- "Continuous use": a team can keep importing pilot data, comparing versions, and converging toward a decision-ready flagship plan.

## Multi-agent design

Six specialists, coordinated by a router, instead of one fixed scripted path:

| Agent | Role |
|---|---|
| **Coordinator** | Routes intent, picks which agents run, decides the output shape (chat / report / full plan). |
| **Retrieval** | Pulls relevant evidence from the workspace via Azure AI Search (hybrid semantic + keyword). |
| **Analysis** | Scores feasibility on a five-dimension rubric; every score must cite evidence. |
| **Market** | Researches real competitors via MCP and Foundry native web search; strictly labeled as *market-inferred*. |
| **Auditor** | Reviews the analysis; if it finds material quality gaps, it routes the work **back** for revision. |
| **Producer** | Generates the PDF proposal, concept image, and audio summary. |

### The audit ⇄ revise loop (Microsoft Agent Framework)

This is the heart of "a system that actually judges, not a straight-line script." The auditor's verdict drives a **conditional Agent execution graph** built with the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (`agent-framework-core`):

- `verdict = needs-revision` → carry the auditor's feedback **back** to the analysis agent and re-analyze.
- `verdict = pass` → converge and finalize.
- Revisions are **bounded** (a configurable cap), so the loop always terminates.

Routing is decided at runtime by the auditor's conclusion via conditional edges — it is *code-controlled judgment*, not a hand-written `if`. We reuse MAF only for orchestration topology; the analysis/audit engines remain our own, so there is zero behavior drift.

### Current implementation boundary

- The first-class MAF team runtime supports four bounded collaboration patterns: `direct`, `concurrent_research`, `specialist_handoff`, and `bounded_review`. Each run reports its selected-agent budget, revision cap, wall-clock time, participant work, and provider-reported token usage; unavailable usage stays `null`. The legacy audit/revise graph remains available in `audit` mode.
- Owner-only monitoring reads one truthful telemetry projection: run records, normalized model-route metadata, owner chargeback, verified outcome records, and APIM/Azure Monitor evidence where configured. The Monitor page never fabricates price, ROI, evaluation, or trace success when those sources are absent.
- APIM governance scope is currently limited to governed text-model calls. Image generation remains visible as out-of-scope coverage but is not counted as governed text cost or route usage until it has a separate gateway path.
- Context Pack optimization is follow-up only. Full analysis, audit repair, and market-research paths continue to use their existing evidence-rich execution context and are never routed through the lightweight candidate path.
- Persisted Context Pack telemetry is metadata-only: scope, version, fingerprint, evidence references, durable-fact identifiers, and bounded fallback state. Raw prompts, raw conversation history, customer evidence text, and Entra claims are excluded.
- Candidate follow-up routes are disabled by default and become eligible only when an offline evaluation summary exists, is fresh, has enough samples, and does not regress evidence coverage or completion.
- Governance separates three different facts: observed model usage/cost, estimated time-value assumptions, and source-linked business outcomes. Outcome records move from `measured` to `verified` only through an explicit reviewer action. Application Insights/OpenTelemetry connectivity is shown separately from Foundry native ROI, which is not connected yet.
- Foundry ROI Preview is not integrated into DataForge today. The product can display verified local ROI from reviewed outcome records and can separately show that Foundry ROI remains unavailable, not configured, or access-dependent.
- Analysis runs form a canonical experiment ledger. Plan and artifact snapshots attach to their source analysis instead of pretending to be new experiments. Each version exposes evidence, metric provenance, evidence deltas, and decision deltas; synthetic, market-only, or untraceable inputs cannot promote the effective verdict.
- Artifact generation runs as persisted background jobs with per-kind partial success, refresh recovery, idempotency, and atomic worker claims. PDF filenames carry the source plan version (`V1`, `V2`, ...).
- The global task center reads durable server task records across analysis, artifact, ingest, and connector flows. It supports truthful queued/running/terminal states, recovery after refresh, cancellation, and retries only where a server-side dispatcher exists.
- Market relevance is a strict source-evidence gate: direct, opportunity-relevant sources are accepted; unrelated, adjacent, consumer-only, and source-free claims are rejected or downgraded to the explicit unavailable contract.
- MAF uses bounded evidence bundles and execution budgets for agents, revisions, and optional market calls. Deterministic evaluation remains fixture evidence, not a production quality or ROI claim.
- Connector records persist only redacted metadata and opaque secret references. With `DF_KEY_VAULT_URL`, deployment identity needs Key Vault secret `get`, `set`, and `delete` permissions; failures do not fall back to session storage. Without it, encrypted process-local secrets are `session_only`, expire after TTL or process restart, and require reconnecting with new credentials.
- Workspace roles (`owner`, `admin`, `editor`, `viewer`) are enforced when `DF_WORKSPACE_RBAC_ENFORCED=1`. The browser uses the Easy Auth-protected Web origin as a same-origin API proxy; the backend accepts identity only when the proxy signs the forwarded principal with `DF_WEB_PROXY_SECRET`. Pending invitations activate only when the matching Easy Auth object and tenant sign in.
- Open-ended Magentic orchestration and Foundry Hosted Agents are not enabled.

### MAF runtime rollout and evaluation

- `DF_MAF_RUNTIME=off` uses the legacy orchestrator, `audit` enables only the existing bounded audit/revise graph, and `full` makes the first-class team runtime eligible.
- When `DF_MAF_RUNTIME` is absent, `DF_USE_MAF=1` maps to `audit` for backward compatibility.
- `DF_MAF_AUTH_MODE=auto` uses the configured Azure OpenAI API key when `AZURE_OPENAI_API_KEY` and `OPENAI_ENDPOINT` are available, otherwise it uses the Foundry project managed identity. Set `api_key` or `managed_identity` to require one mode explicitly. Azure Responses API-key routing defaults to API version `preview` unless `AZURE_OPENAI_API_VERSION` is set.
- `DF_MAF_TRAFFIC_PERCENT` accepts `0..100`. Canary assignment uses a stable hash of workspace ID and conversation ID; it never routes on a business, dataset, industry, or demo name. Keep it at `0` through full tests, deterministic evaluation, backend image import/start smoke, broad review, and a separately approved production canary.
- A MAF construction or runtime failure before its first live event emits fallback evidence and invokes the legacy execution path exactly once. After MAF output begins, MAF owns the terminal response and legacy execution is not restarted. Optional market failure degrades to workspace-only evidence; it does not trigger a full replay.
- Every FULL request that needs workspace data uses authoritative backend retrieval and typed corpus/evidence/rubric contracts; evidence must be non-empty and trace to a retrieved hit. Feasibility and audit output are Pydantic-validated with one contract-correction retry, and the existing rubric, evidence verifier, and typed pre/post-audit guardrail results remain authoritative. `full_package` still runs the producer for PDF, image, plan, and audio delivery.
- MAF events and participant spans are emitted live. Token fields come only from runtime/provider responses, unknown usage remains `null`, workflow wall time is separate from summed participant work, and model-produced telemetry dictionaries are ignored.
- The pinned stable packages are `agent-framework-core==1.11.0`, `agent-framework-foundry==1.10.1`, and `agent-framework-orchestrations==1.0.0`.
- Run the connector-free gate with `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`. Its report declares `measurement_scope='deterministic_harness'` and `production_quality_claim=false`. Groundedness and unsupported-claim rate are fixture/reference-propagation contract checks, not production answer quality measurements. Other metrics are measured only when harness observations exist; unavailable values, including tokens without usage telemetry, remain `null`/`unknown`.
- Run the integrated P2-A gate with `python eval/run_p2_a_acceptance.py --output generated-outputs/p2-a-acceptance.json`. Its machine-readable report includes baseline, market relevance, MAF, task, and connector gates with evidence kind, sample count, production-claim permission, failed reasons, and input lineage. Fixture checks leave latency and tokens as `unmeasured`.
- Run the P2-B Azure governance gate with `python eval/run_p2_b_acceptance.py --output generated-outputs/p2-b-acceptance.json`. It checks trace configuration, trace delivery, local ROI state, Foundry ROI state, chargeback lineage, invitation claim matching, audit redaction, and authorization. The default offline report validates local contracts only: Azure Monitor delivery remains `unmeasured`, Foundry native ROI remains `not_configured`, and `production_claim_allowed` stays `false`. Caller-provided source names, timestamps, IDs, result digests, or self-declared attestations are untrusted and cannot promote a gate. A production claim requires a separate trusted provider verifier with an immutable Azure Monitor or Foundry binding for the expected workspace/run/correlation/build/revision and observation time; this deterministic command cannot make that claim.

The release and evolution design is documented in [`docs/superpowers/specs/2026-07-11-dataforge-release-and-evolution-design.md`](docs/superpowers/specs/2026-07-11-dataforge-release-and-evolution-design.md).

## Key capabilities

- **Discovers non-obvious opportunities** — nobody tells it "do site selection"; it infers the opportunity from the evidence in your data.
- **Self-auditing** — analysis is reviewed and revised before you ever see it; the whole judgment is visible step-by-step in the UI.
- **Evidence-grounded & honest** — every verdict hangs on clickable citations; market inferences are kept strictly separate from workspace facts; missing evidence means an honest downgrade, never a fabricated "feasible."
- **Plan iteration → flagship** — feed real pilot metrics (conversion, ARPU, price) back in as *measured* values, regenerate the next version, and diff v1 vs v2 to converge on a company flagship plan. It's a tool an enterprise keeps using, not a one-shot demo.
- **Durable artifacts** — downloadable PDF proposal, product concept image, and spoken executive summary are generated as recoverable background jobs; successful files remain available even when another artifact type fails.
- **Governed collaboration** — Entra-backed actors are attributed in audit/usage views, workspace roles can enforce read/edit/admin boundaries, and business outcomes retain source and reviewer lineage.
- **Fully traceable** — every run is persisted with audit/revision tags; any historical run can be reopened and replayed, citations still hoverable.

## Azure integration (Pro Code)

Eleven Azure-native services across the stack — all code-controlled, observable, and extensible:

| Layer | Service | Use |
|---|---|---|
| Intelligence | Azure OpenAI (gpt-5.1) | Multi-agent reasoning, structured outputs |
| Intelligence | Azure AI Foundry | Agent Service base + native web search |
| Intelligence | Azure AI Search | Hybrid retrieval (RAG), evidence grounding |
| Data | Azure Blob Storage | Artifacts / runs / sessions persistence |
| Data | Azure Cache for Redis | Feasibility result caching |
| Security | Azure AI Content Safety | Prompt Shield (injection defense) |
| Security | Microsoft Entra ID | Auth (Easy Auth) |
| Experience | Azure AI Speech | Audio summary generation |
| Ops | Azure Container Apps | Containerized rolling deployment |
| Ops | Azure Container Registry | Image build & hosting |
| Ops | Application Insights | OpenTelemetry distributed tracing |

Protocols / frameworks: **Microsoft Agent Framework · MCP · A2A (roadmap)**.

## Tech stack

- **Backend:** Python · FastAPI · SSE streaming · Microsoft Agent Framework (`agent-framework-core==1.11.0`, `agent-framework-foundry==1.10.1`, `agent-framework-orchestrations==1.0.0`) · Azure SDKs
- **Frontend:** React · Vite · live streaming UX
- **Infra:** Terraform (modular) · Azure Container Apps · ACR
- **Observability:** OpenTelemetry → Application Insights

## Repository layout

```
backend/      FastAPI app, orchestrator, MAF audit loop, Azure clients, feasibility engine
web/          React + Vite frontend (streaming UI, Agent execution view, plan iteration)
agents/       Agent prompts (analyst, auditor, …)
mcp-market/   MCP market-lookup tool
ingest/       Document ingestion / indexing
infra/        Terraform modules and dev environment
eval/         Routing / quality evaluation harnesses
docs/         Design decisions and evaluation evidence
workspaces/   Synthetic demo corpora (fictional companies; safe to publish)
```

## Run it

### Backend (local smoke)

```bash
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt -r mcp-market/requirements.txt
cp backend/.env.example backend/.env   # fill in your own values
.venv/bin/python -m pytest
```

The backend has mock-safe fallbacks where Azure resources are not configured, so it can run partially offline.

### Governance audit storage contract

Audit persistence is deliberately excluded from the general mock/local fallback policy. Local audit storage runs only with `DF_AUDIT_LOCAL_MODE=1`; Azure Container Apps and `DF_ENVIRONMENT=production` reject that flag and fail closed if durable audit storage is unavailable.

Production uses two dedicated private containers. `dataforge-audit` holds current append-blob segments plus retained non-authoritative source blobs and has protected append writes enabled. At rotation, the backend reads the complete bounded source under its exact validated ETag, signs its SHA-256 together with the physical stream identity, copies it to `dataforge-audit-sealed`, then independently reads and hashes the canonical block blob. Creator and cross-replica-winner paths both require exact digest equality. Full hashing occurs only once per segment seal, not per event. The sealed container has protected append writes disabled, so completed segments cannot receive authoritative appends. Both containers require locked immutability policies and the same active indefinite legal-hold tag. The account must also have Blob versioning, Blob soft delete, and container soft delete enabled. The backend verifies both exact container contracts through Azure Resource Manager on every audit access, subject to a 60-second bounded cache, by using `DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID`; its managed identity therefore needs `Reader` on the storage account in addition to `Storage Blob Data Contributor`. Missing controls, a missing stream after it was anchored, a truncated stream, or a head older than the signed monotonic receipt is an operational failure, never an empty ledger.

The ledger stores segmented append blobs and a separate signed append-only head stream. Every event carries a server-generated event ID, a key ID, a revision, and a chained HMAC. `DF_AUDIT_HMAC_KEYS` is a JSON object whose values are base64-encoded secrets that decode to at least 32 bytes; `DF_AUDIT_HMAC_ACTIVE_KEY_ID` selects the active entry. Keep previous entries during rotation so old events remain verifiable. The Terraform Container App accepts only a versionless Key Vault secret URI for this JSON (`audit_hmac_keyring_secret_uri`), creates a dedicated user-assigned identity, and grants it `Key Vault Secrets User` on `audit_key_vault_id` before creating the app. Never put key values in Terraform variables, source, logs, or API payloads.

Terraform refuses to plan the storage module unless `audit_immutability_locked=true` and `audit_immutability_lock_confirmation="LOCK_DATAFORGE_AUDIT_WORM"` are both set. Locking both policies is irreversible and protects the containers/account from deletion for the retention period, so release provisioning must review this confirmation intentionally. The legal-hold tag is indefinite until an authorized operator explicitly removes it.

### Frontend

```bash
cd web
npm install
npm run dev
```

### Infrastructure

```bash
cd infra/envs/dev
cp terraform.tfvars.example terraform.tfvars   # fill in your own values
terraform init && terraform apply
```

## Configuration

- `backend/.env.example` — all backend environment variables (Azure endpoints, keys, feature flags). Copy to `.env`.
- `infra/envs/dev/terraform.tfvars.example` — deployment identifiers. Copy to `terraform.tfvars`.
- `.env`, `*.tfvars`, and `*.tfstate*` are git-ignored. **Never commit real keys or subscription IDs.**

Key feature flags: `DF_MAF_RUNTIME` (`off`, `audit`, or `full`), `DF_MAF_AUTH_MODE` (`auto`, `api_key`, or `managed_identity`), `DF_MAF_TRAFFIC_PERCENT` (stable canary percentage), `DF_USE_MAF` (legacy compatibility mapping to `audit`), `DF_MAF_MAX_REVISIONS` (revision cap), `DF_AUDIT_STRICT_GATE` (legacy conservative gate), `DF_WEB_MARKET` (Foundry web search), `DF_WORKSPACE_RBAC_ENFORCED` (workspace role enforcement), `DF_WEB_PROXY_SECRET` (shared secret for the Web-to-backend identity proxy), `DF_ARTIFACT_JOB_STALE_SECONDS` (interrupted-job recovery window), `DF_SEPARATE_ANALYSIS_CONVERSATIONS` (candidate-gated separation of autonomous analysis runs from human message history; default `0`, enable `1` only with the matching frontend revision), `DF_MODEL_ROUTE_ALLOWLIST` / `DF_DEFAULT_MODEL_ROUTE` (server-owned text-route allowlist and default route), and `DF_CONTEXT_EVALUATION_SUMMARY_PATH` / `DF_CONTEXT_EVALUATION_STALE_DAYS` (offline evaluation gate inputs for candidate follow-up routing).

## Monitoring and model routing

- `Monitor` is an owner-only surface. Backend authorization decides access for both navigation and API reads; the frontend only reflects that server-backed permission.
- `models` and `routes` show different but related truths. `models` counts normalized model execution records that actually carried deployment or usage metadata. `routes` additionally preserves executions whose route is only known as `unknown`, so route totals may exceed model totals.
- `coverage.governed_text_calls` is the governed text-call floor from normalized run metadata, and it can be raised by APIM evidence when that evidence is configured and verified for the requested window.
- Missing pricing or business outcome evidence must stay `unavailable` or `pending_verification`; a number appears only when the underlying evidence exists.
- The context-optimization status in Monitor comes from the offline gate summary only: `status`, `sample_count`, `evaluator_version`, and `eligible`. It is not a claim that a candidate route is live in production.

## Configuration

- `backend/.env.example` — all backend environment variables (Azure endpoints, keys, feature flags). Copy to `.env`.
- `infra/envs/dev/terraform.tfvars.example` — deployment identifiers. Copy to `terraform.tfvars`.
- `.env`, `*.tfvars`, and `*.tfstate*` are git-ignored. **Never commit real keys or subscription IDs.**

Key feature flags: `DF_MAF_RUNTIME` (`off`, `audit`, or `full`), `DF_MAF_AUTH_MODE` (`auto`, `api_key`, or `managed_identity`), `DF_MAF_TRAFFIC_PERCENT` (stable canary percentage), `DF_USE_MAF` (legacy compatibility mapping to `audit`), `DF_MAF_MAX_REVISIONS` (revision cap), `DF_AUDIT_STRICT_GATE` (legacy conservative gate), `DF_WEB_MARKET` (Foundry web search), `DF_WORKSPACE_RBAC_ENFORCED` (workspace role enforcement), `DF_WEB_PROXY_SECRET` (shared secret for the Web-to-backend identity proxy), `DF_ARTIFACT_JOB_STALE_SECONDS` (interrupted-job recovery window), `DF_SEPARATE_ANALYSIS_CONVERSATIONS` (candidate-gated separation of autonomous analysis runs from human message history; default `0`, enable `1` only with the matching frontend revision), `DF_MODEL_ROUTE_ALLOWLIST` / `DF_DEFAULT_MODEL_ROUTE` (server-owned text-route allowlist and default route), and `DF_CONTEXT_EVALUATION_SUMMARY_PATH` / `DF_CONTEXT_EVALUATION_STALE_DAYS` (offline evaluation gate inputs for candidate follow-up routing).
## Responsible AI

- **Prompt-injection defense** via Azure AI Content Safety on inputs.
- **Source grading red line:** external market info is labeled *market-inferred* and is never promoted to *workspace-confirmed fact*.
- **No self-deception:** when evidence is insufficient, the verdict is downgraded honestly; the auditor only raises gaps that evidence can support, and never fabricates them.
- Demo corpora are **synthetic** (fictional companies) and clearly labeled as such.

---

*DataForge isn't a chatbot demo. It's a Pro-Code multi-agent system that discovers opportunities, audits itself, and compounds enterprise value over time.*
