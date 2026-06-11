# Run Log

| Time | WP | Action | Result |
|---|---|---|---|
| 2026-06-10 | WP0 | Initialized independent repository under `dataforge-agent`. | Started. |
| 2026-06-10 | WP0 | Checked required repository paths. | Passed. |
| 2026-06-10 | WP1 | Wrote Terraform modules for RG, monitoring, storage, search, speech, ACR, and Container Apps. | Pending validation. |
| 2026-06-10 | WP1 | First Terraform apply. | Partial success; fixed Container Apps Log Analytics ID and moved Search to `eastus` after eastus2 capacity error. |
| 2026-06-10 | WP1 | Terraform apply and output key check. | Passed; Search created in `eastus`, all other application resources in `eastus2`. |
| 2026-06-10 | WP2 | Started demo corpus and RAG indexing implementation. | In progress. |
| 2026-06-10 | WP2 | Ran `python ingest\\build_index.py`, `search_smoke.py`, and `feasibility_smoke.py`. | Passed. |
| 2026-06-10 | WP3 | Started backend tool service implementation. | In progress. |
| 2026-06-10 | WP3 | Built and deployed backend Container App. | Passed cloud health and search smoke. |
| 2026-06-10 | WP4 | Started MCP market server implementation. | In progress. |
| 2026-06-10 | WP4 | Built and deployed MCP market Container App. | Passed health, HTTP tool, and MCP JSON-RPC smoke. |
| 2026-06-10 | WP5 | Started Azure Speech narration implementation. | In progress. |
| 2026-06-10 | WP5 | Injected Speech/Search secrets as Container App secrets and validated narration. | Passed with Azure Speech WAV artifact. |
| 2026-06-10 | WP6 | Started Foundry agent definitions. | In progress. |
| 2026-06-10 | WP6 | Created six Foundry prompt-agent versions and ran verification. | Passed `foundry_get`, strict function schema check, and six-agent `responses_smoke`. |
| 2026-06-10 | WP7 | Started coordinator-driven orchestration implementation. | In progress. |
| 2026-06-10 | WP7 | Ran local and cloud `/api/chat` orchestration smoke tests. | Passed product/corpus routing, clarify short-circuit, MCP trace, and auditor revise/pass path. |
| 2026-06-10 | WP8 | Wired producer tools and ran full-package cloud smoke. | Passed with downloadable PDF, deterministic concept PNG, and Azure Speech WAV. |
| 2026-06-10 | WP9 | Started three-column trace UI from generated concept. | In progress. |
| 2026-06-10 | WP9 | Built React/Vite UI and ran Playwright visual/workflow smoke. | Passed: no console errors, 3 artifact links, 20 trace events, 6 agents visible, desktop/mobile screenshots saved. |
| 2026-06-10 | WP10 | Added router evals and structured trace logging, then deployed backend. | Passed 10 routing cases at 100% accuracy/consistency; App Insights traces returned `dataforge_trace` events. |
| 2026-06-10 | WP11 | Started end-to-end demo script and runbook. | In progress. |
| 2026-06-10 | WP11 | Ran `scripts/demo_e2e.py` against the cloud backend. | Passed all demo checks: clarify, 6 agents, MCP, full package artifacts, Azure Speech, auditor revise/pass, honest no. |
| 2026-06-11 | WP-A | Replaced scripted feasibility/auditor conclusions with Foundry Responses calls and schema validation. | Passed `python eval/next_stage_wp_a_eval.py`; result saved to `docs/wp_a_next_stage_eval.json`. |
| 2026-06-11 | WP-B | Reworked React UI for Chinese local-first workflow and wired previously dead controls. | Passed `npm run build` and Browser interaction checks; committed UI screenshots and `docs/wp_b_ui_eval.json`. |
| 2026-06-11 | WP-C | Added Excel adapter, sample spreadsheets, mixed workspace manifests, Search schema sheet/row fields, and Chinese analyzer. | Passed `python eval/next_stage_wp_c_eval.py`; cloud index returned Excel `sheet`/`row` hits and Foundry produced an excel-corpus feasibility report. |
| 2026-06-11 | WP-C | Rebuilt and deployed backend Container App image `dataforge-backend:next-20260611-2315`. | Passed cloud `/api/health` and cloud `/api/chat` for `excel-corpus` with 2 model responses, final summary, and no errors. |
| 2026-06-11 | WP-R1/R2 | Implemented post-review fixes: empty evidence deterministic downgrade, CJK-friendly local search without fixed Chinese dictionary, markdown language detection, and threadpool wrapping for blocking calls. | Local `python eval/revise_order_eval.py` passed; first frame latency 0.001s; Chinese Excel hit included `sheet=QualitySignals,row=2`; zero-hit query produced no `event:error`. |
| 2026-06-11 | WP-R1/R2 | Deployed `dataforge-backend:revise-20260611-r1r2b`, changed health/tool endpoints to avoid blocking request handling, and set backend Container App to 0.5 CPU, 1Gi memory, min replica 1. | Cloud `python eval/revise_order_eval.py --api-base https://ca-dataforge-backend.thankfultree-c0fc8321.eastus2.azurecontainerapps.io` passed; first frame 0.00005s; warm in-stream health 0.425s; public new-connection health baseline recorded as 1.759s. |
| 2026-06-11 | WP-R3 | Merged PR #1 frontend into `main`, then kept `web/` frozen while upgrading backend agent execution to Foundry prompt-agent `agent_reference` tool loops. | `df-feasibility-analyst` and `df-auditor` produced `mode=foundry_agent_service`, `response_id`, usage, and autonomous `search_pack_context` tool calls in SSE/model metadata. |
| 2026-06-11 | WP-R3 | Added `user-excel-corpus` external Excel workspace using `DF_USER_EXCEL_PATH`; ingested the local Chinese workbook into Azure Search without committing the source Excel file. | Uploaded 189 rows; Chinese search returned `source_file=external/user-chinese-workbook.xlsx`, concrete `sheet` and `row`; `python eval/wp_r3_agent_service_eval.py` passed and saved `docs/wp_r3_agent_service_eval.json`. |
| 2026-06-11 | WP-R3 | Fixed final text sentence-end cleanup for empty-evidence and normal gap summaries. | `python eval/revise_order_eval.py` passed; empty-evidence final text no longer contains `。。`. |
| 2026-06-11 | WP-R3 | Built and deployed backend image `dataforge-backend:wp-r3-20260611-agent-service`. | Cloud `/api/health` passed; cloud `/api/chat` for `user-excel-corpus` emitted autonomous feasibility/auditor `search_pack_context` calls, both model responses used `mode=foundry_agent_service`, verdict was `conditional`, first hit included `sheet`/`row`, and `docs/wp_r3_cloud_smoke.json` passed. |
