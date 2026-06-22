# Autonomous Decisions

| Time | WP | Decision | Reason |
|---|---|---|---|
| 2026-06-10 | WP0 | Use `C:\Users\12140\Documents\your-foundry-projectect\dataforge-agent` as the local independent repository path. | The Linux path in the spec is not available in this Windows workspace; this preserves the required new-repository boundary. |
| 2026-06-10 | WP0 | Keep implementation text and docs mostly English. | Avoid encoding drift from the source documents while preserving product behavior and Chinese demo content where it matters. |
| 2026-06-10 | WP1 | Use `eastus` for AI Search while keeping the application resource group in `eastus2`. | Azure returned `InsufficientResourcesAvailable` for new Search services in `eastus2`; region is parameterized and reversible. |
| 2026-06-10 | WP4 | Validate MCP server directly in WP4 and defer Foundry agent MCP binding to WP6. | `df-market-researcher` does not exist until WP6; the server contract is machine-validated now and agent integration will be verified when agents are created. |
| 2026-06-10 | WP5 | Inject Speech key as a Container App secret for TTS while keeping it out of git. | Speech REST returned 401 for AAD token auth; key is stored only in Azure Container App secrets and remains parameterized. |
| 2026-06-10 | WP5 | Inject Search key as a Container App secret after bearer-token Search calls returned 403. | Keeps the demo path stable while preserving the no-secrets-in-git rule; Managed Identity role assignment remains in place for later hardening. |
| 2026-06-10 | WP6 | Verify Prompt Agent definitions with Foundry `agents.get` plus Responses API smoke calls. | The current Azure AI Projects SDK exposes Prompt Agent versioning separately from a direct per-agent run API; this keeps agent definitions visible in Foundry and verifies model/prompt viability with a repeatable cloud smoke. |
| 2026-06-10 | WP7 | Set `MCP_MARKET_URL` directly on the backend Container App during deployment. | The original Terraform module did not wire the MCP app hostname back into backend env; direct env update keeps the deployed demo working and can be folded into Terraform later. |
| 2026-06-10 | WP8 | Generate the concept image deterministically inside the backend instead of calling the image model. | The WP requires a usable concept artifact; a dependency-free PNG keeps the cloud demo reliable while the image-model path remains a later enhancement. |
| 2026-06-10 | WP9 | Use Playwright screenshots for frontend verification. | The in-app browser control tool was not exposed in this session; Playwright provided repeatable desktop/mobile screenshots and workflow checks. |
| 2026-06-10 | WP10 | Emit every SSE event as a `dataforge_trace` Application Insights trace. | This keeps UI trace and cloud telemetry aligned and gives WP11 a queryable audit trail without adding a separate tracing store. |
