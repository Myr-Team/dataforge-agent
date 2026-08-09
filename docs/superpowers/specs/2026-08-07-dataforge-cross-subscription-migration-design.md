# DataForge Cross-Subscription Migration Design

**Date:** 2026-08-07

## Goal

Recreate the complete DataForge production estate in the target Azure subscription, migrate persistent data and governed configuration, validate the current production release, then publish the verified FinOps candidate at commit `5107ab7`. Switch users to the new Azure Container Apps hostname only after authenticated and operational acceptance passes. Delete the source DataForge resources only after a reversible observation window and an exact deletion-manifest review.

## Confirmed Decisions

- Use a parallel rebuild and copy strategy. Do not attempt a bulk cross-subscription resource move.
- Preserve the current production behavior before introducing the newer application release.
- Use the new Azure Container Apps default hostname rather than a custom domain.
- Reuse the existing Microsoft Entra application registration for Easy Auth.
- Allow a 10–20 minute read-only maintenance window for the final data sync.
- Observe the target for at least 65 minutes before source deletion.
- Never store subscription identifiers, credentials, access tokens, secret values, database passwords, or production payloads in Git, documentation, migration manifests, or command output.

## Current Production Baseline

The active application estate is centered on `rg-dataforge-dev` and includes:

- Container Apps environment `cae-dataforge-dev`.
- Container Apps `ca-dataforge-web`, `ca-dataforge-backend`, and `ca-dataforge-mcp`.
- Scheduled jobs `job-dataforge-finops-apim`, `job-dataforge-finops-rollup`, and `job-dataforge-finops-retention`.
- ACR `acrdataforgedev`.
- Storage accounts `stdataforgedev` and `dfconn06301636449`.
- SQL server `df-sql-conn-06301641478` with user databases `df_connector_demo` and `df_lineage`.
- Redis Enterprise `redis-dataforge-dev`.
- Search `srch-dataforge-dev`.
- Speech `speech-dataforge-dev` and Content Safety `cs-dataforge-dev`.
- Key Vault `kvdflineage833244`.
- API Management `dfmonapim721`.
- Application Insights `appi-dataforge-dev` and Log Analytics `log-dataforge-dev`.
- Azure Communication Services and Email resources used for FinOps notifications.
- Managed identity `id-dataforge-finops-jobs`.

The backend also uses the Foundry account and project under `Agent-Demo-Fuzh`. The active gateway configuration points to `dfmonapim721`. The separate `dataforge-ai-gateway-0711` resource is DataForge-labelled and must be exported and recreated unless execution-time traffic and dependency evidence proves it is an obsolete duplicate and the user explicitly accepts archival instead.

The current production images are immutable digest references associated with the `prodcbc3f1` release family. The newer verified application candidate is commit `5107ab7` on `codex/finops-risk-stage-ui`. The dirty `codex/p2-productization` checkout is not a migration input.

## Target Architecture

Create the same two logical resource groups in the target subscription:

- `rg-dataforge-dev` for the application, data, monitoring, notification, and active gateway stack.
- `Agent-Demo-Fuzh` for the Foundry account, project, agents, and model deployments.

Resource-group-local names remain unchanged where Azure permits it. Globally unique service names receive one generated target suffix recorded in the non-secret migration manifest. The application configuration consumes the generated endpoints rather than assuming source names.

Preserve the current regional distribution during this migration:

- East US 2: Container Apps, ACR, Redis Enterprise, monitoring, speech, Content Safety, and primary storage.
- East US: Azure AI Search.
- West US 3: Azure SQL Database.
- Global: Communication Services and Email.

Region consolidation is outside this migration because combining subscription and region changes would make failure attribution and rollback unsafe.

## Migration Sequence

### 1. Preflight

Before creating resources:

- Confirm write permissions in the target subscription without printing scope identifiers.
- Register or verify the required Azure resource providers.
- Confirm regional availability and quota for Container Apps, Redis Enterprise, APIM Standard v2, Search, Communication Services, and each Foundry model deployment.
- Export source resource configuration with all secret values removed.
- Record active revisions, image digests, traffic weights, scale settings, ingress settings, Easy Auth shape, job schedules, and RBAC role names.
- Record an exact source deletion manifest containing resource type, resource group, and resource name only.

Any missing permission, unavailable SKU, unavailable model deployment, or unresolved source dependency stops the migration before source configuration changes.

### 2. Target Infrastructure

Create target resource groups and services using Terraform where the repository already defines the resource. Use typed Azure CLI or ARM operations for services that are not represented in Terraform. Do not import source Terraform state into the target subscription.

Recreate:

- Container Apps environment, apps, jobs, ingress, scale, and observability integration.
- ACR and immutable image repositories.
- Storage accounts, containers, lifecycle rules, and audit immutability controls.
- SQL logical server and database service tiers.
- Redis Enterprise with an empty cache.
- Search service and index schema.
- Foundry account, project, model deployments, and repository-defined agents.
- Both DataForge API Management services unless the obsolete-gateway exception is explicitly approved.
- Key Vault, managed identities, RBAC assignments, monitoring, alerts, Communication Services, and Email.

Use new managed identities and target-resource RBAC assignments. Reuse the tenant application or service principal only where the application contract requires it. Never copy source Azure resource identifiers into target configuration.

### 3. Secure Configuration Transfer

Transfer secret-bearing settings directly from the source control plane to the target control plane in memory. Do not echo values, interpolate them into logged commands, or write them to local files. Validate only secret names, target references, and successful resource revision changes.

Container Apps secrets, APIM named values, storage credentials, database credentials, provider credentials, HMAC key rings, and the Easy Auth provider secret remain write-only throughout the migration.

The target web app reuses the current Entra application registration. The new callback URI is:

```text
https://<target-web-fqdn>/.auth/login/aad/callback
```

The user will add the exact generated URI under Microsoft Entra ID > App registrations > Authentication > Web. The old redirect URI remains until source deletion and rollback expiry.

### 4. Persistent Data Migration

#### Blob Storage

Perform an initial complete copy and a final incremental synchronization during maintenance. Migrate workspace data, artifacts, audit containers, connector data, and required application metadata. Recreate immutable storage policies before copying sealed audit content.

Do not copy the Easy Auth token store because cookies and tokens are bound to the old hostname and target resource. Do not copy transient transcription content after confirming that no active transcription job depends on it.

Acceptance compares container names, blob counts, total bytes, and bounded sample hashes without exposing blob contents.

#### Azure SQL Database

Create rehearsal copies of `df_connector_demo` and `df_lineage` in the target server. During the maintenance window, create final transactionally consistent cross-subscription database copies after source writes stop. Remap database users and verify schema revisions, table counts, and bounded row-count totals without exporting sensitive rows.

Only the two user databases migrate. The source `master` database is not copied.

#### Search and Redis

Recreate Search indexes from the authoritative Blob and SQL data. Validate schema, index count, and representative application queries.

Redis is transient cache state. Start the target Redis instance empty and verify one genuine miss-to-hit path after cutover. Never copy or combine Redis cache telemetry with gateway accounting.

### 5. Application Release Stages

Import the exact current production backend, web, and MCP images by digest into the target ACR. Deploy them as the first target candidate and verify behavior against the migrated target services.

After the production replica passes, build immutable images from commit `5107ab7`, deploy zero-traffic candidate revisions, and rerun backend, frontend, API contract, browser, authenticated, and mobile acceptance. Promote backend before web. Keep production execution gates at their existing safe values unless a separately verified source setting already enables them.

### 6. Maintenance and Cutover

The formal maintenance window starts only after a rehearsal proves the final synchronization can complete within 20 minutes.

At cutover:

1. Pause the three source FinOps jobs.
2. Disable source web and backend external ingress to stop new writes.
3. Complete the final Blob synchronization.
4. Create and validate the final SQL copies.
5. Point target apps and jobs only at target resources.
6. Add the target Easy Auth callback URI and wait for Entra propagation.
7. Validate target authentication and application workflows.
8. Publish the new target web URL.
9. Start target jobs and begin the observation window.

If the copy exceeds the maintenance budget or any required acceptance fails, re-enable source ingress and jobs. Do not delete or alter the source data services.

## Acceptance Gates

The target must pass all of the following:

- Backend health, web health, and MCP health.
- Current production functionality before the candidate upgrade.
- Full Python, Node, Vite, and Playwright regression for `5107ab7`.
- Desktop and mobile visual acceptance of Cost Management, ROI, Risk and Optimization, evidence drawers, and Operations AI.
- Easy Auth login, logout, callback, token store, trusted claims, workspace role enforcement, and member access denial outside authorized scopes.
- Workspace, conversation, run, artifact, connector, and audit retrieval.
- SQL schema and bounded count reconciliation.
- Blob container, count, byte, and sample-hash reconciliation.
- Foundry model and agent invocation using target deployments.
- APIM governed request, correlation, token evidence, and target-only configuration.
- Real Redis miss-to-hit behavior.
- FinOps request ingestion, five-minute reconciliation, hourly rollup, cost estimate, cache evidence, and request-specific AI explanation.
- Communication Services test email to the configured administrator when the target sender is ready.
- No secret-bearing output and no target configuration referencing source Azure resources.

Observe for at least 65 minutes so the target completes multiple APIM reconciliation executions and one hourly rollup. Critical logs must remain zero for authentication loops, storage authorization, SQL connectivity, provider authentication, job failure, and cross-tenant access.

## Rollback

Before source deletion, rollback consists of:

1. Stop target scheduled jobs.
2. Re-enable source backend and web ingress.
3. Re-enable source scheduled jobs.
4. Direct users back to the old web URL.
5. Preserve the target resources for diagnosis without allowing new writes.

The old Easy Auth redirect URI remains configured until rollback is no longer required.

## Source Deletion

Deletion begins only after every acceptance gate passes, the 65-minute observation completes, and target backup evidence exists.

- Delete only resources present in the reviewed source deletion manifest.
- Delete `rg-dataforge-dev` only after confirming every resource in the group is DataForge-owned.
- In `Agent-Demo-Fuzh`, delete only the migrated DataForge Foundry and gateway resources. Do not delete the group or unrelated demonstrations.
- Do not remove an audit legal hold or locked immutability policy. If Azure blocks deletion, retain the protected storage resource and report the residual cost and retention condition.
- After deletion, list remaining source resources and confirm that no DataForge Container App, job, managed environment, Redis, APIM, SQL database, Search, Storage, ACR, Foundry, Key Vault, monitoring, or Communication resource continues billing unexpectedly.

## Evidence Package

Keep a local, uncommitted migration evidence directory containing sanitized resource inventories, command exit codes, image digests, data reconciliation counts, health results, authenticated acceptance notes, target URLs, traffic state, rollback commands, and deletion results. Never include credentials, tenant identifiers, subscription identifiers, raw user identities, prompts, responses, or production records.
