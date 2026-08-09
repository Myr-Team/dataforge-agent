# DataForge Cross-Subscription Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recreate the complete DataForge production estate in the target Azure subscription, migrate persistent data and governed configuration, validate the current production release, publish commit `5107ab7`, cut users over to the new Container Apps hostname, and remove only the verified source DataForge resources.

**Architecture:** Build a parallel target estate with the same logical resource groups and a fixed globally unique suffix `myr0807`. Copy immutable images and persistent data in advance, validate current production behavior, then release `5107ab7`. Use a rehearsed 10–20 minute maintenance window for the final Blob and SQL copy, followed by at least 65 minutes of observation before source deletion.

**Tech Stack:** Azure CLI, Azure Container Apps, ACR, Blob Storage, Azure SQL Database, Redis Enterprise, Azure AI Search, Azure AI Foundry, API Management, Key Vault, Azure Communication Services, Terraform modules already in the repository, Python 3.11, pytest, Node.js, Vite, Playwright, PowerShell 5.1.

## Global Constraints

- Never print, document, commit, or persist subscription identifiers, tenant identifiers, object identifiers, credentials, tokens, secret values, SQL passwords, raw identities, prompts, responses, or production payloads.
- Refer to subscriptions by Azure display name only. The source is the current CLI subscription; the target display name is `Microsoft Azure ai1-1`.
- Keep the source production estate unchanged until the formal maintenance task.
- Never reuse source Terraform state in the target subscription.
- Keep `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0`, `DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED=0`, and `DF_FINOPS_ACTIONS_ENABLED=0` unless the verified source production revision already has a different accepted value.
- Preserve `codex/p2-productization` and `.superpowers/sdd` work in place. Never clean, reset, rebase, or commit those files.
- Use the target Container Apps default hostname. Reuse the existing Easy Auth Entra application and add the generated callback URI manually.
- Do not copy the Easy Auth token store, transient transcription data, or Redis cache state.
- Do not remove legal holds or locked audit immutability policies.
- Do not delete source resources until all acceptance gates pass and the 65-minute observation window completes.

---

### Task 1: Establish an isolated migration execution branch and sanitized evidence workspace

**Files:**
- Reference: `docs/superpowers/specs/2026-08-07-dataforge-cross-subscription-migration-design.md`
- Reference: `docs/superpowers/plans/2026-08-07-dataforge-cross-subscription-migration.md`
- Local only: `output/migration/2026-08-07-target-subscription/`

**Interfaces:**
- Consumes: approved design commit `874d058` and application candidate `5107ab7`.
- Produces: isolated branch `codex/dataforge-subscription-migration`, sanitized evidence directory, and source/target display-name variables.

- [ ] **Step 1: Create the isolated worktree from the approved design commit**

Run from the repository worktree:

```powershell
git worktree add -b codex/dataforge-subscription-migration C:\Users\12140\Documents\Agent-Demo-project-worktrees\codex-dataforge-subscription-migration 874d058
```

Expected: a new worktree on `codex/dataforge-subscription-migration`; the original worktree remains unchanged.

- [ ] **Step 2: Create a local evidence directory**

```powershell
$MigrationRoot = 'C:\Users\12140\Documents\Agent-Demo-project-worktrees\codex-dataforge-subscription-migration'
$EvidenceRoot = Join-Path $MigrationRoot 'output\migration\2026-08-07-target-subscription'
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
```

Expected: the ignored evidence directory exists and `git status --short` does not list its contents as tracked changes.

- [ ] **Step 3: Establish subscription display-name variables without emitting identifiers**

```powershell
$SourceSubscriptionName = az account show --query name -o json | ConvertFrom-Json
$TargetSubscriptionName = 'Microsoft Azure ai1-1'
$TargetState = az account list --query "[?name=='Microsoft Azure ai1-1'].state | [0]" -o tsv
if ($TargetState -ne 'Enabled') { throw 'Target subscription is not enabled.' }
```

Expected: target state is `Enabled`; no subscription identifier is printed.

### Task 2: Add and test a sanitized migration-manifest utility

**Files:**
- Create: `scripts/azure/dataforge_migration_manifest.py`
- Create: `tests/test_dataforge_migration_manifest.py`

**Interfaces:**
- Consumes: Azure CLI JSON read from stdin or files under the ignored evidence directory.
- Produces: `sanitize_resource_inventory(payload)`, `resource_names_by_group(payload)`, and `assert_no_sensitive_values(payload)`.

- [ ] **Step 1: Write failing tests for redaction and exact resource selection**

```python
from scripts.azure.dataforge_migration_manifest import (
    assert_no_sensitive_values,
    resource_names_by_group,
    sanitize_resource_inventory,
)


def test_inventory_keeps_only_safe_resource_fields():
    source = [{
        "id": "/subscriptions/hidden/resourceGroups/rg-dataforge-dev/providers/Microsoft.App/containerApps/ca-dataforge-web",
        "name": "ca-dataforge-web",
        "type": "Microsoft.App/containerApps",
        "resourceGroup": "rg-dataforge-dev",
        "location": "eastus2",
        "properties": {"secret": "never-copy"},
    }]
    assert sanitize_resource_inventory(source) == [{
        "name": "ca-dataforge-web",
        "type": "Microsoft.App/containerApps",
        "resource_group": "rg-dataforge-dev",
        "location": "eastus2",
    }]


def test_manifest_groups_only_explicit_dataforge_resources():
    rows = [
        {"name": "ca-dataforge-web", "type": "Microsoft.App/containerApps", "resource_group": "rg-dataforge-dev", "location": "eastus2"},
        {"name": "unrelated-demo", "type": "Microsoft.Storage/storageAccounts", "resource_group": "Agent-Demo-Fuzh", "location": "eastasia"},
    ]
    assert resource_names_by_group(rows, {"rg-dataforge-dev": None}) == {"rg-dataforge-dev": ["ca-dataforge-web"]}


def test_sensitive_identifiers_are_rejected():
    try:
        synthetic_identifier = "00000000-" + "0000-0000-0000-000000000000"
        assert_no_sensitive_values({"subscription_id": synthetic_identifier})
    except ValueError as exc:
        assert "sensitive" in str(exc).lower()
    else:
        raise AssertionError("expected sensitive manifest rejection")
```

- [ ] **Step 2: Run the tests and verify they fail**

```powershell
python -m pytest tests/test_dataforge_migration_manifest.py -q
```

Expected: collection fails because `scripts.azure.dataforge_migration_manifest` does not exist.

- [ ] **Step 3: Implement the bounded manifest utility**

```python
import re
from collections import defaultdict

_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_FORBIDDEN_KEYS = {"id", "subscription_id", "tenant_id", "principal_id", "client_id", "secret", "value", "token", "password"}


def sanitize_resource_inventory(payload):
    rows = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "name": str(item.get("name") or ""),
            "type": str(item.get("type") or ""),
            "resource_group": str(item.get("resourceGroup") or item.get("resource_group") or ""),
            "location": str(item.get("location") or ""),
        })
    assert_no_sensitive_values(rows)
    return rows


def resource_names_by_group(rows, allowed_groups):
    grouped = defaultdict(list)
    for row in rows:
        group = row.get("resource_group")
        if group in allowed_groups:
            grouped[group].append(row.get("name"))
    return {group: sorted(name for name in names if name) for group, names in sorted(grouped.items())}


def assert_no_sensitive_values(payload):
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_KEYS:
                    raise ValueError("sensitive manifest key")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif _GUID.search(str(value)):
            raise ValueError("sensitive manifest value")
    visit(payload)
```

- [ ] **Step 4: Run the focused tests**

```powershell
python -m pytest tests/test_dataforge_migration_manifest.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit the utility**

```powershell
git add scripts/azure/dataforge_migration_manifest.py tests/test_dataforge_migration_manifest.py
git commit -m "test(azure): add sanitized migration manifest"
```

### Task 3: Execute target permission, provider, quota, and global-name preflight

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/preflight.json`
- Local only: `output/migration/2026-08-07-target-subscription/source-resources.json`

**Interfaces:**
- Consumes: source default subscription, target display name, fixed suffix `myr0807`.
- Produces: a passing preflight report and target resource group `rg-dataforge-dev` only after permission checks pass.

- [ ] **Step 1: Verify required providers are available in the target subscription**

```powershell
$Providers = @('Microsoft.App','Microsoft.ContainerRegistry','Microsoft.Storage','Microsoft.Sql','Microsoft.Cache','Microsoft.Search','Microsoft.CognitiveServices','Microsoft.ApiManagement','Microsoft.KeyVault','Microsoft.Insights','Microsoft.OperationalInsights','Microsoft.Communication')
foreach ($Provider in $Providers) {
  $State = az provider show --subscription $TargetSubscriptionName --namespace $Provider --query registrationState -o tsv
  if ($State -notin @('Registered','Registering')) { az provider register --subscription $TargetSubscriptionName --namespace $Provider --wait }
}
```

Expected: every provider reports `Registered`.

- [ ] **Step 2: Check fixed global names without creating services**

```powershell
$Names = [ordered]@{
  acr='acrdataforgemyr0807'; storage='stdataforgemyr0807'; connectorStorage='dfconnmyr0807';
  keyVault='kvdfmyr0807'; sql='dfsqlmyr0807'; search='srch-dataforge-myr0807';
  apim='dfmonapim-myr0807'; legacyApim='dataforge-ai-gateway-myr0807';
  speech='speech-dataforge-myr0807'; contentSafety='cs-dataforge-myr0807';
  redis='redis-dataforge-myr0807'; foundry='agent-demo-foundry-myr0807';
  communication='acs-dataforge-myr0807'; email='ecs-dataforge-myr0807'
}
$NameChecks = @(
  ((az acr check-name -n $Names.acr --query nameAvailable -o tsv) -eq 'true'),
  ((az storage account check-name -n $Names.storage --query nameAvailable -o tsv) -eq 'true'),
  ((az storage account check-name -n $Names.connectorStorage --query nameAvailable -o tsv) -eq 'true'),
  ((az keyvault check-name -n $Names.keyVault --query nameAvailable -o tsv) -eq 'true'),
  ((az sql server list --subscription $TargetSubscriptionName --query "length([?name=='$($Names.sql)']) == `0`" -o tsv) -eq 'true')
)
if ($NameChecks -contains $false) { throw 'A fixed target name is unavailable; stop before provisioning.' }
```

Expected: all checked names are available. Remaining service names are validated by their service-specific create validation before creation.

- [ ] **Step 3: Validate write permission with the target resource group**

```powershell
az group create --subscription $TargetSubscriptionName --name rg-dataforge-dev --location eastus2 --tags application=DataForge migration=2026-08-07 environment=production-candidate --output none
az group show --subscription $TargetSubscriptionName --name rg-dataforge-dev --query provisioningState -o tsv
```

Expected: `Succeeded`. If authorization fails, stop without source changes.

- [ ] **Step 4: Capture a sanitized source inventory**

```powershell
$RawInventory = az resource list -o json | ConvertFrom-Json
$RawInventory | ConvertTo-Json -Depth 10 | python -c "import json,sys; from scripts.azure.dataforge_migration_manifest import sanitize_resource_inventory; print(json.dumps(sanitize_resource_inventory(json.load(sys.stdin)), ensure_ascii=False, indent=2))" | Set-Content -Encoding UTF8 (Join-Path $EvidenceRoot 'source-resources.json')
python -m json.tool (Join-Path $EvidenceRoot 'source-resources.json') | Out-Null
```

Expected: sanitized JSON parses; no identifiers or secrets are present.

### Task 4: Provision target core, data, AI, gateway, and notification services

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/provisioning.json`

**Interfaces:**
- Consumes: passing Task 3 preflight and `$Names`.
- Produces: target Azure resources with no production traffic and no source resource references.

- [ ] **Step 1: Create monitoring, ACR, managed identity, Key Vault, and storage**

```powershell
$TargetResourceGroup = 'rg-dataforge-dev'
$TargetFoundryGroup = 'Agent-Demo-Fuzh'
az group create --subscription $TargetSubscriptionName -n $TargetFoundryGroup -l eastasia --output none
az monitor log-analytics workspace create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n log-dataforge-dev -l eastus2 --output none
az monitor app-insights component create --subscription $TargetSubscriptionName -g $TargetResourceGroup -a appi-dataforge-dev -l eastus2 --kind web --application-type web --output none
az acr create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.acr -l eastus2 --sku Basic --admin-enabled false --output none
az identity create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n id-dataforge-finops-jobs -l eastus2 --output none
az keyvault create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.keyVault -l eastus2 --enable-rbac-authorization true --output none
az storage account create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.storage -l eastus2 --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false --output none
az storage account create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.connectorStorage -l eastus2 --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false --output none
foreach($container in @('artifacts','dataforge-audit-preview','dataforge-audit-preview-sealed','dataforge-audit-prod','dataforge-audit-prod-sealed','dataforge-workspaces','workspaces')) {
  az storage container-rm create --subscription $TargetSubscriptionName -g $TargetResourceGroup --storage-account $Names.storage -n $container --public-access off --output none
}
az storage container-rm create --subscription $TargetSubscriptionName -g $TargetResourceGroup --storage-account $Names.connectorStorage -n dataforge-demo --public-access off --output none
```

Expected: Log Analytics, Application Insights, ACR, managed identity, Key Vault, both storage accounts, and required containers report `Succeeded`.

- [ ] **Step 2: Recreate audit immutability configuration before copying audit blobs**

Export only policy type, retention period, legal-hold tag names, and lock state. Apply equivalent target policies without copying source resource identifiers.

Expected: target sealed containers have at least the source retention strength. If a locked policy cannot be reproduced, stop before copying audit data.

- [ ] **Step 3: Create SQL, Search, Redis, Speech, Content Safety, and Communication Services**

```powershell
$MigrationPrincipal = az account show --query user.name -o tsv
az sql server create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.sql -l westus3 --enable-ad-only-auth --external-admin-name DataForgeMigrationAdmin --external-admin-principal-type ServicePrincipal --external-admin-sid $MigrationPrincipal --output none
az search service create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.search -l eastus --sku basic --partition-count 1 --replica-count 1 --output none
az redisenterprise create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.redis -l eastus2 --sku Balanced_B0 --output none
az cognitiveservices account create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.speech -l eastus2 --kind SpeechServices --sku S0 --custom-domain $Names.speech --yes --output none
az cognitiveservices account create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.contentSafety -l eastus2 --kind ContentSafety --sku S0 --custom-domain $Names.contentSafety --yes --output none
az communication create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.communication -l global --data-location UnitedStates --output none
az communication email create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.email -l global --data-location UnitedStates --output none
```

Expected: both target SQL databases can be created or copied; Search and Redis report `Succeeded`; Speech and Content Safety endpoints respond to management-plane reads; Communication and Email report connected or an honest `not_ready` state that does not block core cutover.

- [ ] **Step 4: Recreate Foundry and model deployments**

```powershell
$SourceDeployments = az cognitiveservices account deployment list -g Agent-Demo-Fuzh -n Agent-Demo-Foundry-fuzh -o json | ConvertFrom-Json
az cognitiveservices account create --subscription $TargetSubscriptionName -g $TargetFoundryGroup -n $Names.foundry -l eastus2 --kind AIServices --sku S0 --custom-domain $Names.foundry --yes --output none
foreach($deployment in $SourceDeployments) {
  az cognitiveservices account deployment create --subscription $TargetSubscriptionName -g $TargetFoundryGroup -n $Names.foundry --deployment-name $deployment.name --model-name $deployment.properties.model.name --model-version $deployment.properties.model.version --model-format $deployment.properties.model.format --sku-name $deployment.sku.name --sku-capacity $deployment.sku.capacity --output none
}
```

Create the project child resource with the same location and repository-defined agent names using the current `Microsoft.CognitiveServices/accounts/projects` API version returned by `az provider show`; reject any request body containing a source resource identifier.

Expected: target deployment inventory matches source logical deployment names. Any unavailable model or quota stops migration before source maintenance.

- [ ] **Step 5: Recreate active and legacy DataForge APIM services**

```powershell
$PublisherName = az apim show -g rg-dataforge-dev -n dfmonapim721 --query publisherName -o tsv
$PublisherEmail = az apim show -g rg-dataforge-dev -n dfmonapim721 --query publisherEmail -o tsv
az apim create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n $Names.apim -l eastus2 --sku-name StandardV2 --publisher-name $PublisherName --publisher-email $PublisherEmail --output none
$LegacyPublisherName = az apim show -g Agent-Demo-Fuzh -n dataforge-ai-gateway-0711 --query publisherName -o tsv
$LegacyPublisherEmail = az apim show -g Agent-Demo-Fuzh -n dataforge-ai-gateway-0711 --query publisherEmail -o tsv
az apim create --subscription $TargetSubscriptionName -g $TargetFoundryGroup -n $Names.legacyApim -l eastus2 --sku-name StandardV2 --publisher-name $LegacyPublisherName --publisher-email $LegacyPublisherEmail --output none
```

For every source API name returned by `az apim api list`, export OpenAPI into the ignored evidence directory, replace only source backend hostnames with target hostnames, import into the matching target APIM, then copy raw XML policy with `az apim api policy create`. Transfer secret named values only through the secure helper implemented in Task 5.

Expected: management-plane API and policy hashes match after replacing target endpoints and resource references.

- [ ] **Step 6: Create the Container Apps environment, apps, and jobs with ingress disabled**

```powershell
az containerapp env create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n cae-dataforge-dev -l eastus2 --logs-destination none --output none
az containerapp create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n ca-dataforge-backend --environment cae-dataforge-dev --image mcr.microsoft.com/azuredocs/containerapps-helloworld:latest --ingress internal --target-port 8000 --min-replicas 1 --max-replicas 3 --output none
az containerapp create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n ca-dataforge-web --environment cae-dataforge-dev --image mcr.microsoft.com/azuredocs/containerapps-helloworld:latest --ingress internal --target-port 8080 --min-replicas 1 --max-replicas 3 --output none
az containerapp create --subscription $TargetSubscriptionName -g $TargetResourceGroup -n ca-dataforge-mcp --environment cae-dataforge-dev --image mcr.microsoft.com/azuredocs/containerapps-helloworld:latest --ingress internal --target-port 8000 --min-replicas 1 --max-replicas 1 --output none
```

Create each job with the imported current-production backend image, the source cron expression, `--replica-timeout` and `--replica-retry-limit` values, but set a disabled temporary cron schedule `0 0 31 2 *` until cutover. Replace the placeholder app images in Task 6 before enabling any external ingress.

Expected: all resources exist in the target subscription, but no customer request or scheduled job can write data.

### Task 5: Add and test an in-memory secret transfer helper

**Files:**
- Create: `scripts/azure/dataforge_secret_transfer.py`
- Create: `tests/test_dataforge_secret_transfer.py`

**Interfaces:**
- Consumes: source Container Apps secret-list ARM response held in memory, target Key Vault URL, target secret names.
- Produces: target Key Vault secret versions and target Container Apps Key Vault references; never returns secret values.

- [ ] **Step 1: Write tests proving values never reach logs or return values**

```python
from scripts.azure.dataforge_secret_transfer import transfer_secrets


def test_transfer_returns_names_only(capsys):
    written = {}
    result = transfer_secrets(
        [{"name": "provider-key", "value": "never-print-this"}],
        lambda name, value: written.setdefault(name, value),
    )
    assert result == ["provider-key"]
    assert written == {"provider-key": "never-print-this"}
    assert "never-print-this" not in capsys.readouterr().out


def test_transfer_rejects_invalid_secret_names():
    try:
        transfer_secrets([{"name": "../bad", "value": "hidden"}], lambda *_: None)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid secret name must fail")
```

- [ ] **Step 2: Run the tests and verify they fail**

```powershell
python -m pytest tests/test_dataforge_secret_transfer.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement the minimal in-memory transfer core**

```python
import re

_SAFE_NAME = re.compile(r"^[a-z0-9-]{1,127}$")


def transfer_secrets(records, writer):
    written = []
    for record in records:
        name = str(record.get("name") or "")
        value = record.get("value")
        if not _SAFE_NAME.fullmatch(name) or not isinstance(value, str) or not value:
            raise ValueError("invalid secret record")
        writer(name, value)
        written.append(name)
    return sorted(written)
```

- [ ] **Step 4: Add Azure SDK adapters without logging request bodies**

Use `DefaultAzureCredential`, the Container Apps ARM list-secrets action, and `azure.keyvault.secrets.SecretClient.set_secret`. Return only written secret names and target secret version success booleans. Never enable HTTP body logging.

- [ ] **Step 5: Run focused and security tests**

```powershell
python -m pytest tests/test_dataforge_secret_transfer.py -q
rg -n --pcre2 "print\(.*value|logging\..*value|subscription[_-]?id|tenant[_-]?id" scripts/azure/dataforge_secret_transfer.py
```

Expected: tests pass and the search returns no matches.

- [ ] **Step 6: Commit the secret helper**

```powershell
git add scripts/azure/dataforge_secret_transfer.py tests/test_dataforge_secret_transfer.py
git commit -m "feat(azure): add write-only migration secret bridge"
```

### Task 6: Import current production images and apply target-only configuration

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/image-digests.json`
- Local only: `output/migration/2026-08-07-target-subscription/configuration-checks.json`

**Interfaces:**
- Consumes: source image digests, target ACR, target endpoints, target Key Vault references.
- Produces: inactive target revisions matching current production behavior.

- [ ] **Step 1: Import backend, web, and MCP images by digest**

Use `az acr import` from `acrdataforgedev.azurecr.io` into `acrdataforgemyr0807`. Record source and target digests only.

Expected: target manifest digests equal source digests.

- [ ] **Step 2: Transfer write-only secrets and configure target references**

Run the Task 5 helper with all console output restricted to secret names and success booleans. Configure new Key Vault-backed Container Apps secrets and APIM secret named values.

Expected: every source-required secret name has a target version; no secret value appears in terminal output or evidence files.

- [ ] **Step 3: Apply target endpoints and preserve safe feature flags**

Replace Foundry, OpenAI, APIM, Key Vault, Redis, Storage, Search, Speech, Content Safety, Communication, SQL, monitoring, backend-upstream, and portal values with target endpoints. Preserve logical model deployment names and accepted feature flags.

Expected: a configuration audit reports zero source hostnames and zero source resource references.

- [ ] **Step 4: Configure target managed identities and RBAC**

Grant only required roles for ACR pull, Storage Blob Data access, Key Vault secrets, SQL/Monitor reads, APIM management, and job execution. Do not grant subscription Owner to application identities.

Expected: each data-plane smoke succeeds with the corresponding target identity; no broad role remains unexplained.

### Task 7: Perform initial persistent-data migration and reconciliation rehearsal

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/data-rehearsal.json`

**Interfaces:**
- Consumes: target storage, SQL, Search, and source read access.
- Produces: initial Blob copy, SQL rehearsal databases, rebuilt Search indexes, and measured final-sync duration.

- [ ] **Step 1: Copy persistent Blob containers**

Copy `workspaces`, `dataforge-workspaces`, `artifacts`, all four audit containers, and `dataforge-demo`. Exclude `easyauth-tokenstore` and `transcription-temp`.

Expected: target container names, blob counts, total bytes, and bounded sample hashes match the source snapshot.

- [ ] **Step 2: Create cross-subscription SQL rehearsal copies**

Use `az sql db copy` or the documented T-SQL cross-server copy flow for `df_connector_demo` and `df_lineage`. Never copy `master`.

Expected: target databases are `ONLINE`; schema versions, table counts, and bounded row-count totals match.

- [ ] **Step 3: Rebuild Search indexes and verify representative queries**

Expected: schema and document totals match the authoritative migrated data; representative queries return the same safe identifiers and counts.

- [ ] **Step 4: Rehearse final incremental synchronization and record duration**

Expected: Blob delta plus final SQL copy completes within 20 minutes. If it does not, stop before maintenance and revise the window.

### Task 8: Validate an exact current-production replica in the target

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/current-production-acceptance.json`

**Interfaces:**
- Consumes: migrated target data and imported current-production images.
- Produces: healthy target revisions and a current-production acceptance record.

- [ ] **Step 1: Activate target ingress for candidate testing without publishing the URL**

Expected: web and backend default FQDNs resolve; backend is reachable only through the intended proxy path; target jobs remain paused.

- [ ] **Step 2: Validate health and current production APIs**

Check health, workspace listing, conversations, runs, artifacts, connectors, monitoring, FinOps bootstrap, model providers, identity governance, and request evidence.

Expected: all required endpoints return accepted shapes and target-only evidence.

- [ ] **Step 3: Validate Foundry, APIM, SQL, Blob, Search, and Redis**

Run one real analysis, one governed model request, one SQL-backed query, one Blob-backed workspace read, one Search query, and one Redis miss-to-hit pair.

Expected: each dependency is target-owned; APIM correlation joins the application request; Redis remains separate from gateway accounting.

- [ ] **Step 4: Keep source production active**

Expected: no user-visible cutover has occurred and source rollback remains immediate.

### Task 9: Build and validate the latest application release `5107ab7`

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/latest-candidate-acceptance.json`

**Interfaces:**
- Consumes: commit `5107ab7` plus migration documentation commits, target ACR and inactive target revisions.
- Produces: immutable backend/web candidate images and complete regression evidence.

- [ ] **Step 1: Run the complete local regression suite**

```powershell
python -m pytest -q
Push-Location web
node --test
npm run build
$env:DF_PLAYWRIGHT_PORT='5340'
npx playwright test
Pop-Location
git diff --check
```

Expected baseline: Python at least `1758 passed, 1 skipped`; Node at least `292 passed`; Playwright at least `61 passed`; Vite succeeds; diff check is clean.

- [ ] **Step 2: Build immutable backend and web images in the target ACR**

Tag images with the exact commit and record resulting digests. Do not use mutable `latest` tags for deployment evidence.

Expected: both ACR builds succeed and digests are recorded.

- [ ] **Step 3: Deploy zero-traffic backend then web candidate revisions**

Expected: both revisions are `Healthy` and `Running`; existing target production-replica revision remains available for rollback.

- [ ] **Step 4: Run authenticated desktop/mobile and API acceptance against the candidate**

Expected: Cost Management, ROI, Risk and Optimization, evidence, Operations AI, model settings, member budgets, provider connections, and responsive layouts behave as verified locally.

### Task 10: Configure Easy Auth and execute the formal maintenance cutover

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/easy-auth-checklist.md`
- Local only: `output/migration/2026-08-07-target-subscription/cutover.json`

**Interfaces:**
- Consumes: healthy latest target candidate, rehearsed data sync, existing Entra app registration.
- Produces: authenticated target production URL and stopped source writes.

- [ ] **Step 1: Provide the exact target callback URI to the user**

Write only this generated URI to the checklist:

```text
https://TARGET_WEB_FQDN/.auth/login/aad/callback
```

`TARGET_WEB_FQDN` is the actual FQDN returned by the target `ca-dataforge-web` resource, not a manually guessed value.

- [ ] **Step 2: Wait for the user to add the callback URI in Entra**

Expected: user confirms the Web redirect URI is saved. Retain the old URI.

- [ ] **Step 3: Validate Easy Auth before maintenance**

Check unauthenticated redirect, authenticated `/.auth/me`, logout, token-store behavior, trusted claims, owner/admin/member workspace rules, and cross-workspace denial.

Expected: no redirect loop or `AADSTS50011`; authorization boundaries match source production.

- [ ] **Step 4: Start the maintenance window**

Pause source FinOps jobs, disable source web/backend ingress, and record exact timestamps and prior settings for rollback.

Expected: new source writes stop and source configuration remains reversible.

- [ ] **Step 5: Run final Blob and SQL synchronization**

Expected: reconciliation matches and completes within 20 minutes. Any mismatch re-enables source ingress and jobs and aborts cutover.

- [ ] **Step 6: Promote target backend then web and start target jobs**

Expected: target traffic is 100% on the verified latest revisions, target jobs are enabled, and the new URL is ready to publish.

### Task 11: Observe, reconcile, and delete only verified source resources

**Files:**
- Local only: `output/migration/2026-08-07-target-subscription/observation.json`
- Local only: `output/migration/2026-08-07-target-subscription/deletion-manifest.json`
- Local only: `output/migration/2026-08-07-target-subscription/deletion-results.json`

**Interfaces:**
- Consumes: successful cutover, sanitized source manifest, target acceptance evidence.
- Produces: 65-minute observation evidence and a source estate with no unintended DataForge billing resources.

- [ ] **Step 1: Observe for at least 65 minutes**

Verify multiple five-minute reconciliation runs, one hourly rollup, real user authentication, real analysis, APIM correlation, Redis miss-to-hit, cost ingestion, Operations AI, and critical-log count zero.

Expected: no source endpoint or resource reference appears in target telemetry or configuration.

- [ ] **Step 2: Review the exact deletion manifest**

Include every resource in `rg-dataforge-dev` only after proving it is DataForge-owned. In `Agent-Demo-Fuzh`, include only migrated Foundry and DataForge gateway resources. Reject any manifest containing a subscription identifier or unrelated resource.

Expected: `assert_no_sensitive_values` passes and the manifest matches the approved scope.

- [ ] **Step 3: Preserve rollback backups and audit constraints**

Confirm target SQL backups, Blob reconciliation evidence, image digests, APIM exports, Foundry deployment inventory, and source audit immutability status.

Expected: a locked audit resource is removed from the deletion command and reported as retained.

- [ ] **Step 4: Delete source DataForge resources**

Delete the approved `rg-dataforge-dev` resource group and only the approved DataForge resources in `Agent-Demo-Fuzh`. Do not delete either subscription or unrelated resource groups.

Expected: deletion operations succeed or return a documented immutable-retention block.

- [ ] **Step 5: Verify post-deletion state and target health**

List remaining source DataForge-named resources, target health, target jobs, target traffic, and critical logs.

Expected: no unexpected source DataForge billing resource remains; target production remains healthy and authenticated.

- [ ] **Step 6: Commit the sanitized final runbook updates only**

Do not commit the evidence directory. Commit only reusable migration code and documentation after a final secret scan.

```powershell
git diff --check
git status --short
$AccessKeyPattern = 'AKIA' + '[0-9A-Z]{16}'
$PrivateKeyPattern = '-----BEGIN ' + '.*PRIVATE KEY-----'
$GuidPattern = '[0-9a-fA-F]{8}-' + '[0-9a-fA-F]{4}-' + '[0-9a-fA-F]{4}-' + '[0-9a-fA-F]{4}-' + '[0-9a-fA-F]{12}'
$SensitiveFiles = @('scripts/azure/dataforge_migration_manifest.py','scripts/azure/dataforge_secret_transfer.py','docs/superpowers/specs/2026-08-07-dataforge-cross-subscription-migration-design.md','docs/superpowers/plans/2026-08-07-dataforge-cross-subscription-migration.md','tests/test_dataforge_migration_manifest.py','tests/test_dataforge_secret_transfer.py')
$SensitiveHits = rg -l --pcre2 "$AccessKeyPattern|$PrivateKeyPattern|$GuidPattern" -- $SensitiveFiles
if($LASTEXITCODE -eq 0){ throw "Sensitive pattern detected in migration files: $($SensitiveHits -join ',')" }
```

Expected: diff check passes; the identifier/secret scan returns no migration-file matches; user-owned WIP remains uncommitted.
