# DataForge Build Progress

| WP | Scope | Status | Timestamp | Acceptance |
|---|---|---|---|---|
| WP0 | Repository, `.gitignore`, reuse manifest skeleton | done | 2026-06-10 | Structure check passed: required repository paths exist. |
| WP1 | Terraform application resources | done | 2026-06-10 | `terraform apply` succeeded; output JSON contains required resource endpoints and deployment names. |
| WP2 | Demo corpus and workspace-aware RAG index | done | 2026-06-10 | Uploaded 10 chunks to AI Search; smoke retrieved SaaS signals and medical constraints; health diagnosis verdict is `not_yet_feasible`. |
| WP3 | Backend tool service and schemas | done | 2026-06-10 | Deployed backend image `dataforge-backend:wp3-20260610-1517`; `/api/health` 200 and `/api/search-pack-context` returned 3 demo hits. |
| WP4 | Market MCP server | done | 2026-06-10 | Deployed `dataforge-mcp:wp4-20260610-1520`; health, HTTP `market_lookup`, and MCP `tools/call` returned competitor data. |
| WP5 | Speech narration | done | 2026-06-10 | `/api/narrate-summary` returned `mode=azure-speech`; downloaded WAV artifact had RIFF header and 376044 bytes. |
| WP6 | Six Foundry agents, prompts, tool schemas | done | 2026-06-10 | Created 6 Foundry prompt-agent versions; `foundry_get` verified all agents; `responses_smoke` returned non-empty role outputs for all 6 agents. |
| WP7 | Coordinator-driven orchestrator | done | 2026-06-10 | Deployed `dataforge-backend:wp7-20260610-1658`; cloud `/api/chat` routed product vs corpus questions differently, returned `clarify` for vague input, emitted MCP `market_lookup` count 4, and showed auditor revise/pass for health diagnosis. |
| WP8 | Producer artifacts: PDF, image, audio | done | 2026-06-10 | Deployed `dataforge-backend:wp8-20260610-1715`; cloud `full_package` produced downloadable PDF 3260 bytes, PNG 11676 bytes, and Azure Speech WAV 500444 bytes. |
| WP9 | Three-column trace UI | done | 2026-06-10 | React/Vite UI built successfully; Playwright run against local UI produced 3 cloud artifact links, trace count 20, all 6 agents visible, and desktop/mobile screenshots in `docs/assets`. |
| WP10 | Deterministic router, evals, tracing | done | 2026-06-10 | Routing eval passed 10 cases with accuracy 1.0 and coordinator/router consistency 1.0; deployed `dataforge-backend:wp10-20260610-1728`; App Insights `traces` query returned `dataforge_trace` events. |
| WP11 | End-to-end demo script | done | 2026-06-10 | `python scripts/demo_e2e.py` passed against cloud: clarify, 6 agents, MCP, 3 artifacts, Azure Speech, auditor revise/pass, and honest `not_yet_feasible` all true. |
| WP-A | Real Foundry feasibility and auditor path | done | 2026-06-11 | `python eval/next_stage_wp_a_eval.py` passed: feasibility/auditor emitted real `response_id` and token usage, unsupported non-medical corpus returned weaker verdict, auditor model returned revise for unsupported draft, and orchestrator grep guard passed. |
| WP-B | Local-first Chinese trace UI | done | 2026-06-11 | `npm run build` passed; Browser verified local health probe, no fake starter messages, document click search, tab switching, trace filtering, and model traces with `response_id`/tokens. Screenshots saved in `docs/assets/wp_b_ui_*.png`. |
| WP-C | Excel ingestion and sheet/row grounded search | done | 2026-06-11 | `python eval/next_stage_wp_c_eval.py` passed: Excel rows converted to records, Search schema uses `zh-Hans.microsoft`, cloud search returned concrete `sheet`/`row` hits, excel-corpus produced a distinct real model feasibility report, and cloud backend image `dataforge-backend:next-20260611-2315` passed `/api/chat`. |
