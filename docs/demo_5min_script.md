# DataForge 5-Minute Demo Script

## 0:00-0:30 Pain

Enterprises sit on product manuals, internal wikis, sensor dictionaries, market notes, consent reviews, and support feedback. The question is not "can we chat with the files?" The question is: which data product can this company credibly package, what evidence supports it, and where must the system honestly say no?

## 0:30-2:30 Live Run

Open the UI and run:

```text
Create a full package with PDF, concept image, and audio for a data product from this workspace.
```

Point to the right trace rail:

- `df-coordinator` routes to `full_package`.
- `df-corpus-analyst` calls AI Search with the `demo-corpus` workspace filter.
- `df-market-researcher` calls MCP `market_lookup` and returns competitor data.
- `df-producer` calls `render_pdf_report`, `generate_image`, and `narrate_summary`.
- `df-auditor` checks the structured artifact before final delivery.

Then run:

```text
Can we build a health diagnosis product from this workspace?
```

Highlight the two required memory moments:

- The auditor returns `revise` because medical diagnosis needs clinical validation, consent, and labeled outcomes.
- The final feasibility verdict is `not_yet_feasible`; DataForge refuses to invent support where the workspace lacks evidence.

## 2:30-3:30 Deliverables

Open the artifact buttons:

- PDF report: project proposal generated from the grounded artifact.
- Concept image: product concept visual.
- Audio summary: Azure Speech WAV narration.

The latest automated E2E run downloaded all three artifacts and verified headers:

- PDF starts with `%PDF`.
- Image starts with PNG header.
- Audio starts with `RIFF`.

## 3:30-4:30 Architecture

DataForge is a client-side multi-agent orchestration system:

- Azure AI Foundry prompt agents define the six roles.
- FastAPI backend hosts tool endpoints and the orchestration loop.
- Azure AI Search provides workspace-filtered RAG.
- MCP market server exposes `market_lookup`.
- Azure AI Speech produces the spoken executive summary.
- Application Insights receives `dataforge_trace` events for observability.

## 4:30-5:00 Business Close

This is a reusable proposal accelerator for customer data productization. It turns unstructured customer material into a feasibility package with evidence, gaps, market comparison, and deliverables. The differentiator is trust: the system can say "not yet feasible" and show exactly why.

## Machine Check

Run:

```powershell
python scripts\demo_e2e.py
```

Expected highlights:

- `ok: true`
- `clarify_short_circuit: true`
- `six_agents_visible: true`
- `mcp_visible: true`
- `azure_speech_audio: true`
- `auditor_revise: true`
- `honest_no: true`
