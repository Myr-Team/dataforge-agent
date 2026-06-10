# Autonomous Decisions

| Time | WP | Decision | Reason |
|---|---|---|---|
| 2026-06-10 | WP0 | Use `C:\Users\12140\Documents\Agent-Demo-project\dataforge-agent` as the local independent repository path. | The Linux path in the spec is not available in this Windows workspace; this preserves the required new-repository boundary. |
| 2026-06-10 | WP0 | Keep implementation text and docs mostly English. | Avoid encoding drift from the source documents while preserving product behavior and Chinese demo content where it matters. |
| 2026-06-10 | WP1 | Use `eastus` for AI Search while keeping the application resource group in `eastus2`. | Azure returned `InsufficientResourcesAvailable` for new Search services in `eastus2`; region is parameterized and reversible. |
