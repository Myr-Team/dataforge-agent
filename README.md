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

- The first-class MAF team runtime supports four bounded collaboration patterns: `direct`, `concurrent_research`, `specialist_handoff`, and `bounded_review`. The legacy audit/revise graph remains available in `audit` mode.
- The governance page exposes a clearly labelled DataForge ROI estimate based on token usage and configurable time-value assumptions. Foundry native ROI is not connected yet.
- Plan, artifact, and feedback runs are persisted as version snapshots. A full experiment ledger with source-backed evidence and decision deltas is planned and is not represented as complete today.
- Open-ended Magentic orchestration and Foundry Hosted Agents are not enabled.

### MAF runtime rollout and evaluation

- `DF_MAF_RUNTIME=off` uses the legacy orchestrator, `audit` enables only the existing bounded audit/revise graph, and `full` makes the first-class team runtime eligible.
- When `DF_MAF_RUNTIME` is absent, `DF_USE_MAF=1` maps to `audit` for backward compatibility.
- `DF_MAF_TRAFFIC_PERCENT` accepts `0..100`. Canary assignment uses a stable hash of workspace ID and conversation ID; it never routes on a business, dataset, industry, or demo name.
- A MAF construction or runtime failure emits fallback evidence and invokes the legacy execution path exactly once. Optional market failure degrades to workspace-only evidence; it does not trigger a full replay.
- The pinned stable packages are `agent-framework-core==1.11.0`, `agent-framework-foundry==1.10.1`, and `agent-framework-orchestrations==1.0.0`.
- Run the connector-free gate with `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`. Selection accuracy, groundedness, unsupported-claim rate, latency, tokens, task completion, and fallback rate are measured when observations exist; unavailable values remain `null`/`unknown`.

The release and evolution design is documented in [`docs/superpowers/specs/2026-07-11-dataforge-release-and-evolution-design.md`](docs/superpowers/specs/2026-07-11-dataforge-release-and-evolution-design.md).

## Key capabilities

- **Discovers non-obvious opportunities** — nobody tells it "do site selection"; it infers the opportunity from the evidence in your data.
- **Self-auditing** — analysis is reviewed and revised before you ever see it; the whole judgment is visible step-by-step in the UI.
- **Evidence-grounded & honest** — every verdict hangs on clickable citations; market inferences are kept strictly separate from workspace facts; missing evidence means an honest downgrade, never a fabricated "feasible."
- **Plan iteration → flagship** — feed real pilot metrics (conversion, ARPU, price) back in as *measured* values, regenerate the next version, and diff v1 vs v2 to converge on a company flagship plan. It's a tool an enterprise keeps using, not a one-shot demo.
- **One-click artifacts** — downloadable PDF proposal, a product concept image, and a spoken executive summary.
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

Key feature flags: `DF_MAF_RUNTIME` (`off`, `audit`, or `full`), `DF_MAF_TRAFFIC_PERCENT` (stable canary percentage), `DF_USE_MAF` (legacy compatibility mapping to `audit`), `DF_MAF_MAX_REVISIONS` (revision cap), `DF_AUDIT_STRICT_GATE` (legacy conservative gate), `DF_WEB_MARKET` (Foundry web search).

## Responsible AI

- **Prompt-injection defense** via Azure AI Content Safety on inputs.
- **Source grading red line:** external market info is labeled *market-inferred* and is never promoted to *workspace-confirmed fact*.
- **No self-deception:** when evidence is insufficient, the verdict is downgraded honestly; the auditor only raises gaps that evidence can support, and never fabricates them.
- Demo corpora are **synthetic** (fictional companies) and clearly labeled as such.

---

*DataForge isn't a chatbot demo. It's a Pro-Code multi-agent system that discovers opportunities, audits itself, and compounds enterprise value over time.*
