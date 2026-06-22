# DataForge — turn sleeping data into product opportunities

[简体中文](./README.zh-CN.md)

**DataForge is a multi-agent system that turns an enterprise's existing data into product opportunities** — and then quantifies whether each opportunity is actually feasible, grounded in evidence. Upload your business data; multiple specialist agents collaborate to discover non-obvious, high-value product directions, score their feasibility on a five-dimension rubric, and produce deliverables (a PDF proposal, a concept image, and an audio summary) in one click.

> Built for the Microsoft GCR *AI Agent Frontier Hackathon* — **Track B (Pro Code)**. Orchestration, the feasibility engine, and the guardrails are all written in code; nothing here is a low-code drag-and-drop flow.

---

## The problem

Enterprises sit on mountains of data — field signals, transactions, device logs, environmental context — but beyond their core business, **nobody can say what else that data could become.** Hiring a data-science team to explore it is slow and expensive, so most data just sleeps.

The naive thought that started this project:

> *"I have all this data but can't find any new opportunity in it — so let me throw it all in and just **quantify** what my data is actually worth."*

DataForge automates exactly that.

**Real case that inspired it:** a company built floor-level anti-loss hardware and had piles of on-site signal data. They only ever used it for positioning — until they realized those signals were really *location / foot-traffic intelligence*, and built a store **site-selection app** on top of them that took off. DataForge is built to surface that kind of unexpected-but-defensible second curve from data you already own.

---

## How it works

```
Upload data → auto profiling + indexing
            → multi-agent analysis (retrieval → analysis → market)
            → audit ⇄ revise loop (Microsoft Agent Framework)
            → grounded feasibility verdict (+ citations)
            → one-click artifacts (PDF / concept image / audio)
            → backfill real pilot metrics → iterate toward a flagship plan
```

Everything is streamed to the UI in real time (SSE), so you watch the reasoning happen — not just the final answer.

## Multi-agent design

Six specialists, coordinated by a router, instead of one fixed pipeline:

| Agent | Role |
|---|---|
| **Coordinator** | Routes intent, picks which agents run, decides the output shape (chat / report / full plan). |
| **Retrieval** | Pulls relevant evidence from the workspace via Azure AI Search (hybrid semantic + keyword). |
| **Analysis** | Scores feasibility on a five-dimension rubric; every score must cite evidence. |
| **Market** | Researches real competitors via MCP and Foundry native web search; strictly labeled as *market-inferred*. |
| **Auditor** | Reviews the analysis; if it finds material quality gaps, it routes the work **back** for revision. |
| **Producer** | Generates the PDF proposal, concept image, and audio summary. |

### The audit ⇄ revise loop (Microsoft Agent Framework)

This is the heart of "a system that actually judges, not a straight-line pipeline." The auditor's verdict drives a **conditional workflow** built with the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (`agent-framework-core`):

- `verdict = needs-revision` → carry the auditor's feedback **back** to the analysis agent and re-analyze.
- `verdict = pass` → converge and finalize.
- Revisions are **bounded** (a configurable cap), so the loop always terminates.

Routing is decided at runtime by the auditor's conclusion via conditional edges — it is *code-controlled judgment*, not a hand-written `if`. We reuse MAF only for orchestration topology; the analysis/audit engines remain our own, so there is zero behavior drift.

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

- **Backend:** Python · FastAPI · SSE streaming · Microsoft Agent Framework (`agent-framework-core`) · Azure SDKs
- **Frontend:** React · Vite · live streaming UX
- **Infra:** Terraform (modular) · Azure Container Apps · ACR
- **Observability:** OpenTelemetry → Application Insights

## Repository layout

```
backend/      FastAPI app, orchestrator, MAF workflow, Azure clients, feasibility engine
web/          React + Vite frontend (streaming UI, agent flow, plan iteration)
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

Key feature flags: `DF_USE_MAF` (enable the audit⇄revise workflow), `DF_MAF_MAX_REVISIONS` (revision cap), `DF_AUDIT_STRICT_GATE` (legacy conservative gate), `DF_WEB_MARKET` (Foundry web search).

## Responsible AI

- **Prompt-injection defense** via Azure AI Content Safety on inputs.
- **Source grading red line:** external market info is labeled *market-inferred* and is never promoted to *workspace-confirmed fact*.
- **No self-deception:** when evidence is insufficient, the verdict is downgraded honestly; the auditor only raises gaps that evidence can support, and never fabricates them.
- Demo corpora are **synthetic** (fictional companies) and clearly labeled as such.

---

*DataForge isn't a chatbot demo. It's a Pro-Code multi-agent system that discovers opportunities, audits itself, and compounds enterprise value over time.*
