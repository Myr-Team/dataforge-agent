# Entra member budget and ACS Email candidate runbook

> Operator-run only. This document does not authorize an agent to create Azure
> resources, run SQL, deploy revisions, start jobs, send email, or change
> production traffic. Keep every live item `PENDING — NOT ACCEPTED` until the
> named human approver records the result.

## 0. Release boundary and exact approved inputs

This release provides:

- administrator-only Entra member budget and estimated-cost views;
- pricing coverage that preserves unpriced requests;
- one active Owner/Admin recipient per tenant;
- ACS Email test mail and deduplicated threshold reminders;
- a 15-minute compensating evaluation job.

It does not provide billing reconciliation, user blocking, automatic model
changes, APIM policy changes, or arbitrary recipients. Keep
`DF_FINOPS_ACTIONS_ENABLED=0`.

Before running any command, create a clean isolated worktree or detached
checkout at the approved release commit. Build both contexts only from that
checkout. A human approver must fill and approve every value below. Do not infer
them from an existing subscription. Do not put an email address, subscription
ID, principal ID, token, secret, connection string, or resource ID in Git
evidence.

```powershell
$ApprovedSubscription = '<approved-subscription-name-or-id>'
$ApprovedResourceGroup = '<approved-resource-group>'
$ApprovedAzureRegion = '<approved-container-app-region>'
$ApprovedAcsDataLocation = '<approved-acs-data-location>'
$ApprovedEmailService = '<approved-email-service-name>'
$ApprovedCommunicationService = '<approved-communication-service-name>'
$ApprovedBackendApp = '<approved-backend-container-app>'
$ApprovedWebApp = '<approved-web-container-app>'
$ApprovedContainerAppsEnvironment = '<approved-container-app-environment>'
$ApprovedRegistry = '<approved-acr-name>'
$ApprovedJob = '<approved-member-budget-job-name>'
$ApprovedSenderAddress = '<approved-AzureManagedDomain-sender-address>'
$ApprovedAdminRecipient = '<approved-active-owner-or-admin-email>'
$ApprovedAcsEmailRoleDefinitionId = '<approved-minimum-ACS-email-send-role-definition-id>'
$ApprovedSqlServer = '<approved-sql-server-name>'
$ApprovedSqlDatabase = '<approved-sql-database-name>'
$ApprovedPortalUrl = '<approved-https-member-budget-page-url>'
$ReleaseCommit = '<full-40-character-approved-git-commit>'
$PreviousBackendRevision = '<record-before-candidate>'
$PreviousWebRevision = '<record-before-candidate>'
```

Fail closed if any placeholder remains, the resource group/region/data
residency is not explicitly approved, or the sender and administrator recipient
are not explicitly approved.

```powershell
$variables = Get-Variable Approved*,ReleaseCommit,Previous* |
  ForEach-Object { [string]$_.Value }
if ($variables | Where-Object { -not $_ -or $_ -match '^<.*>$' }) {
  throw 'STOP: approved candidate inputs are incomplete'
}
az account set --subscription $ApprovedSubscription
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: approved subscription selection failed'
}
$ObservedReleaseCommit = (git rev-parse HEAD).Trim()
if (
  $LASTEXITCODE -ne 0 -or
  $ObservedReleaseCommit -cne $ReleaseCommit
) {
  throw 'STOP: checkout does not match the approved release commit'
}
$ReleaseWorkspaceChanges = @(
  git status --porcelain --untracked-files=all
)
if ($LASTEXITCODE -ne 0 -or $ReleaseWorkspaceChanges.Count -ne 0) {
  throw 'STOP: release checkout contains tracked or untracked changes'
}
```

The commit output must exactly equal `$ReleaseCommit`, and the isolated release
checkout must contain no tracked modification and no untracked file. Ignored
files are checked separately by the filesystem-level sensitive-file scan before
either build.

## 1. Create ACS Email resources with managed identity only

Use an authorized operator identity. Never create or retrieve an ACS connection
string or service key.

```powershell
az communication email create `
  --name $ApprovedEmailService `
  --resource-group $ApprovedResourceGroup `
  --location global `
  --data-location $ApprovedAcsDataLocation

az communication email domain create `
  --domain-name AzureManagedDomain `
  --email-service-name $ApprovedEmailService `
  --resource-group $ApprovedResourceGroup `
  --location global `
  --domain-management AzureManaged

az communication create `
  --name $ApprovedCommunicationService `
  --resource-group $ApprovedResourceGroup `
  --location global `
  --data-location $ApprovedAcsDataLocation
```

Resolve the two resource IDs only in the operator shell. Do not copy them into
the candidate record.

```powershell
$EmailDomainId = az communication email domain show `
  --domain-name AzureManagedDomain `
  --email-service-name $ApprovedEmailService `
  --resource-group $ApprovedResourceGroup `
  --query id -o tsv
$CommunicationServiceId = az communication show `
  --name $ApprovedCommunicationService `
  --resource-group $ApprovedResourceGroup `
  --query id -o tsv
if (-not $EmailDomainId -or -not $CommunicationServiceId) {
  throw 'STOP: ACS resource IDs were not resolved'
}
```

Link the Azure Managed Domain to the Communication Services resource without
blindly replacing an existing link. Confirm the API version in the approved
tenant before execution; do not proceed if the provider rejects it. Keep the
pre-change list only in the protected operator record so it can be used for
rollback; resource IDs must not be copied into Git evidence.

```powershell
function Get-ValidatedCommunicationState {
  param([Parameter(Mandatory)][string]$Uri)

  $rawState = & az rest --method get --uri $Uri -o json
  $readExitCode = $LASTEXITCODE
  $rawStateText = ($rawState | Out-String)
  if (
    $readExitCode -ne 0 -or
    [string]::IsNullOrWhiteSpace($rawStateText)
  ) {
    throw 'STOP: ACS pre-read failed or returned an empty response'
  }
  try {
    $state = $rawStateText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw 'STOP: ACS pre-read returned invalid JSON'
  }
  if (
    $null -eq $state -or
    $null -eq $state.PSObject.Properties['properties'] -or
    $null -eq $state.properties -or
    $null -eq $state.properties.PSObject.Properties['linkedDomains']
  ) {
    throw 'STOP: ACS pre-read response is missing properties.linkedDomains'
  }
  $linkedDomains = $state.properties.linkedDomains
  if (
    $null -eq $linkedDomains -or
    $linkedDomains -isnot [System.Array]
  ) {
    throw 'STOP: ACS pre-read linkedDomains must be a JSON array'
  }
  $invalidLinks = @(
    @($linkedDomains) |
      Where-Object {
        $_ -isnot [string] -or
        [string]::IsNullOrWhiteSpace($_)
      }
  )
  if ($invalidLinks.Count -ne 0) {
    throw 'STOP: ACS pre-read linkedDomains has an invalid shape'
  }
  return $state
}

$CommunicationApiVersion = '2023-04-01'
$CommunicationUri = "$CommunicationServiceId?api-version=$CommunicationApiVersion"
$CommunicationBefore = Get-ValidatedCommunicationState -Uri $CommunicationUri
$LinkedDomainsBeforeChange = @(
  $CommunicationBefore.properties.linkedDomains |
    Where-Object { $_ } |
    ForEach-Object { [string]$_ }
)
$LinkedDomainsRollbackJson = ConvertTo-Json `
  -InputObject @($LinkedDomainsBeforeChange) `
  -Compress
# Save $LinkedDomainsRollbackJson in the protected operator change record only.

$MergedLinkedDomains = @(
  $LinkedDomainsBeforeChange + $EmailDomainId |
    Sort-Object -Unique
)
$WouldRemoveDomain = @(
  $LinkedDomainsBeforeChange |
    Where-Object { $_ -notin $MergedLinkedDomains }
)
if ($WouldRemoveDomain.Count -ne 0) {
  throw 'STOP: domain replacement/removal needs separate explicit approval'
}

if ($EmailDomainId -notin $LinkedDomainsBeforeChange) {
  $linkBody = @{ properties = @{ linkedDomains = $MergedLinkedDomains } } |
    ConvertTo-Json -Depth 5 -Compress
  az rest `
    --method patch `
    --uri $CommunicationUri `
    --body $linkBody `
    --headers 'Content-Type=application/json'
  if ($LASTEXITCODE -ne 0) {
    throw 'STOP: ACS domain merge failed; do not replace an existing domain'
  }
}

$CommunicationAfter = Get-ValidatedCommunicationState -Uri $CommunicationUri
$LinkedDomainsAfterChange = @(
  $CommunicationAfter.properties.linkedDomains |
    Where-Object { $_ } |
    ForEach-Object { [string]$_ }
)
$MissingAfterMerge = @(
  $MergedLinkedDomains |
    Where-Object { $_ -notin $LinkedDomainsAfterChange }
)
if (
  $MissingAfterMerge.Count -ne 0 -or
  $EmailDomainId -notin $LinkedDomainsAfterChange
) {
  throw 'STOP: linked domain verification failed; use the protected rollback value'
}
```

This procedure only adds the approved domain while preserving every existing
link. If the service requires replacing or removing any link, stop and obtain a
separate human-approved change and rollback plan; do not adapt this merge
command into a replacement.

The following restore block is for a separately approved rollback in the same
protected operator session. It PATCHes the exact pre-change array, checks the
write, performs another validated GET, and requires an exact set match. Do not
run it during normal candidate setup.

```powershell
$ApprovedRestoreLinkedDomains = Read-Host (
  'Type RESTORE_EXACT_LINKED_DOMAINS_AFTER_APPROVED_ROLLBACK to continue'
)
if (
  $ApprovedRestoreLinkedDomains -cne
  'RESTORE_EXACT_LINKED_DOMAINS_AFTER_APPROVED_ROLLBACK'
) {
  throw 'STOP: linked-domain restore was not explicitly approved'
}
if ($null -eq $LinkedDomainsBeforeChange) {
  throw 'STOP: protected pre-change linkedDomains array is unavailable'
}

$RollbackLinkedDomains = @($LinkedDomainsBeforeChange)
$rollbackBody = @{
  properties = @{
    linkedDomains = $RollbackLinkedDomains
  }
} | ConvertTo-Json -Depth 5 -Compress
az rest `
  --method patch `
  --uri $CommunicationUri `
  --body $rollbackBody `
  --headers 'Content-Type=application/json'
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: linked-domain restore PATCH failed'
}

$RollbackState = Get-ValidatedCommunicationState -Uri $CommunicationUri
$RollbackReadback = @(
  $RollbackState.properties.linkedDomains |
    Where-Object { $_ } |
    ForEach-Object { [string]$_ }
)
$RollbackMissing = @(
  $RollbackLinkedDomains |
    Where-Object { $_ -notin $RollbackReadback }
)
$RollbackUnexpected = @(
  $RollbackReadback |
    Where-Object { $_ -notin $RollbackLinkedDomains }
)
if (
  $RollbackReadback.Count -ne $RollbackLinkedDomains.Count -or
  $RollbackMissing.Count -ne 0 -or
  $RollbackUnexpected.Count -ne 0
) {
  throw 'STOP: linked-domain restore readback does not match the exact prior set'
}
```

Enable the existing backend system-assigned identity and grant only the
separately approved ACS Email data-plane role. Do not grant Owner, Contributor,
or subscription-wide scope.

```powershell
az containerapp identity assign `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --system-assigned
$BackendPrincipalId = az containerapp identity show `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --query principalId -o tsv
if (-not $BackendPrincipalId) {
  throw 'STOP: backend managed identity is unavailable'
}
az role assignment create `
  --assignee-object-id $BackendPrincipalId `
  --assignee-principal-type ServicePrincipal `
  --role $ApprovedAcsEmailRoleDefinitionId `
  --scope $CommunicationServiceId
```

Record only resource names, the role-assignment outcome category, and UTC
timestamps. Do not record IDs.

## 2. Apply the additive Azure SQL migration

Use the approved deployment identity, not the runtime managed identity. If a
temporary single-IP firewall rule is required, create it immediately before the
migration and delete it immediately afterward. Never grant DDL to the runtime
identity.

```powershell
$env:DF_FINOPS_SQL_SERVER = $ApprovedSqlServer
$env:DF_FINOPS_SQL_DATABASE = $ApprovedSqlDatabase
$Migration1StartedAt = (Get-Date).ToUniversalTime().ToString('o')
& python -m backend.finops.migrate
$Migration1ExitCode = $LASTEXITCODE
$Migration1EndedAt = (Get-Date).ToUniversalTime().ToString('o')
if ($Migration1ExitCode -ne 0) {
  throw 'STOP: first additive migration failed; second run was not attempted'
}

$Migration2StartedAt = (Get-Date).ToUniversalTime().ToString('o')
& python -m backend.finops.migrate
$Migration2ExitCode = $LASTEXITCODE
$Migration2EndedAt = (Get-Date).ToUniversalTime().ToString('o')
if ($Migration2ExitCode -ne 0) {
  throw 'STOP: second additive migration failed'
}
```

Verify the following objects through the controlled deployment connection:

- `df_finops.member_budget`;
- `df_finops.notification_setting`;
- `df_finops.budget_alert`;
- the unique alert threshold constraint;
- the request actor/window index.

Retain tables on rollback. Do not drop FinOps evidence.

### 2.1 Repair retained actor attribution before enabling member budgets

This is a mandatory pre-cutover gate for deployments that previously derived
FinOps actor attribution from a non-canonical Entra identifier. Keep all
member-budget, email-alert, action, and external-routing switches disabled
throughout the repair:

```powershell
$env:DF_FINOPS_MEMBER_BUDGETS_ENABLED = '0'
$env:DF_FINOPS_EMAIL_CONFIGURATION_ENABLED = '0'
$env:DF_FINOPS_EMAIL_ALERTS_ENABLED = '0'
$env:DF_FINOPS_ACTIONS_ENABLED = '0'
$env:DF_EXTERNAL_PROVIDER_ROUTING_ENABLED = '0'
```

Choose an explicit UTC window that is fully covered by the retained completed
run ledger and is no longer than 90 days. Start with exactly one bounded
dry-run page:

```powershell
$RepairFrom = '<approved-UTC-start>'
$RepairTo = '<approved-UTC-end>'
$RepairCursor = $null

$DryRunArgs = @(
  '-m', 'backend.finops.actor_ref_repair',
  '--from', $RepairFrom,
  '--to', $RepairTo,
  '--page-size', '100',
  '--max-pages', '1'
)
if ($RepairCursor) {
  $DryRunArgs += @('--cursor', $RepairCursor)
}
& python @DryRunArgs
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: canonical actor attribution dry-run failed'
}
```

The command is dry-run by default. Its JSON must contain aggregate counts and
an opaque `offset_########` cursor only. Stop if output contains a run ID,
tenant ID, Entra object ID, email address, request reference, or exception
body. Record the approved window, page size, aggregate planned count, skip
categories, and `next_cursor`; never record source identities.

Before apply, manually reconcile the planned count with the retained-run
coverage for that same immutable window. Any `detail_unavailable`,
`invalid_record`, `tenant_unavailable`, `model_limit`, or other unexpected skip
must be investigated. Do not introduce a legacy-key lookup or email fallback.

Apply only the reviewed page, using the same window, cursor, page size, and
source snapshot. The SQL connection and HMAC secret must be supplied through
the approved secret-injection path and must never be printed:

```powershell
$ApplyArgs = @(
  '-m', 'backend.finops.actor_ref_repair',
  '--from', $RepairFrom,
  '--to', $RepairTo,
  '--page-size', '100',
  '--max-pages', '1',
  '--apply',
  '--confirm', 'APPLY_CANONICAL_ACTOR_REF_REPAIR'
)
if ($RepairCursor) {
  $ApplyArgs += @('--cursor', $RepairCursor)
}
& python @ApplyArgs
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: canonical actor attribution apply failed'
}
```

The existing request-event MERGE is keyed by `(tenant_ref, request_ref)`.
Repeat the same approved page once and verify that the distinct key count does
not increase. For each page:

- `events_applied` must equal the reviewed eligible event count;
- `write_failed` must be zero;
- missing Entra object IDs must remain `actor_ref IS NULL` and must not be
  derived from email;
- aggregate request-event totals must remain unchanged after the second apply;
- one controlled member's estimated cost must join to the canonical Entra
  directory member without displaying either raw identifier;
- candidate logs and captured output must pass a raw-ID and email scan.

Advance only with the returned `next_cursor`, repeating dry-run review before
each apply. Finish when `has_more` is false, then repeat the final dry-run and
ledger checks for the complete approved window. If the retained source changes
between pages, a count differs, or any join is ambiguous, stop the release.

Member-budget and email-alert flags may be enabled in the zero-traffic
candidate only after the repair is complete, all focused/full tests pass, the
duplicate-key check is clean, and a human reviewer accepts the aggregate
evidence. Rollback returns to the prior immutable application revision and
keeps the feature flags disabled; it does not restore legacy actor references
or delete additive FinOps evidence.

No repair, candidate validation, Azure mutation, or production cutover was
executed while writing this runbook. All live evidence remains **PENDING —
NOT ACCEPTED**.

## 3. Build immutable images

Build from the exact approved commit and record both digests. Before either
build, scan the filesystem rather than Git's tracked-file view: an ignored
`backend/.env` must still stop the release. The scan reports only a count and
never prints file names or contents. The two explicitly allowed files below are
committed non-secret templates/development defaults and remain excluded from
the Docker contexts.

```powershell
$RepositoryRoot = (Resolve-Path '.').Path
$RequiredRootDockerIgnore = @(
  '.git/',
  '**/.git/',
  'node_modules/',
  '**/node_modules/',
  '.playwright-cli/',
  '**/.playwright-cli/',
  '.superpowers/',
  '**/.superpowers/',
  'output/',
  '**/output/',
  'test-results/',
  '**/test-results/',
  '.venv/',
  '**/.venv/',
  'venv/',
  '**/venv/',
  'dist/',
  '**/dist/',
  '__pycache__/',
  '**/__pycache__/',
  '.pytest_cache/',
  '**/.pytest_cache/',
  '.mypy_cache/',
  '**/.mypy_cache/',
  '.ruff_cache/',
  '**/.ruff_cache/',
  '.tox/',
  '**/.tox/',
  '.nox/',
  '**/.nox/',
  'coverage/',
  '**/coverage/',
  'htmlcov/',
  '**/htmlcov/'
)
$RequiredWebDockerIgnore = @(
  '.git',
  '**/.git',
  'node_modules',
  '**/node_modules',
  '.playwright-cli',
  '**/.playwright-cli',
  '.superpowers',
  '**/.superpowers',
  'output',
  '**/output',
  'test-results',
  '**/test-results',
  '.venv',
  '**/.venv',
  'venv',
  '**/venv',
  'dist',
  '**/dist',
  '__pycache__',
  '**/__pycache__',
  '.pytest_cache',
  '**/.pytest_cache',
  '.mypy_cache',
  '**/.mypy_cache',
  '.ruff_cache',
  '**/.ruff_cache',
  '.tox',
  '**/.tox',
  '.nox',
  '**/.nox',
  'coverage',
  '**/coverage',
  'htmlcov',
  '**/htmlcov'
)
$RootDockerIgnoreLines = @(
  Get-Content -LiteralPath (Join-Path $RepositoryRoot '.dockerignore') |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') }
)
$WebDockerIgnoreLines = @(
  Get-Content -LiteralPath (Join-Path $RepositoryRoot 'web\.dockerignore') |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') }
)
if (
  @(
    $RequiredRootDockerIgnore |
      Where-Object { $_ -cnotin $RootDockerIgnoreLines }
  ).Count -ne 0 -or
  @(
    $RequiredWebDockerIgnore |
      Where-Object { $_ -cnotin $WebDockerIgnoreLines }
  ).Count -ne 0
) {
  throw 'STOP: required Docker build-context exclusions are missing'
}

$AllowedNonSecretEnvFiles = @(
  [IO.Path]::GetFullPath((Join-Path $RepositoryRoot 'backend\.env.example')),
  [IO.Path]::GetFullPath((Join-Path $RepositoryRoot 'web\.env.development'))
)
$ExcludedScanSegments = @(
  '\.git\',
  '\node_modules\',
  '\.venv\',
  '\venv\',
  '\dist\',
  '\output\',
  '\test-results\',
  '\.playwright-cli\',
  '\.superpowers\',
  '\.pytest_cache\',
  '\.mypy_cache\',
  '\.ruff_cache\',
  '\.tox\',
  '\.nox\',
  '\coverage\',
  '\htmlcov\'
)
$SensitiveNamePattern = (
  '^(?:\.env(?:\..+)?|id_rsa(?:\..+)?|id_ed25519(?:\..+)?|' +
  'credentials(?:\..+)?|azureProfile\.json|accessTokens\.json)$|' +
  '\.(?:pem|key|pfx|p12|ppk|jks|keystore|tfvars|tfstate(?:\..+)?)$|' +
  '(?:credentials|service[-_]account).*\.json$'
)
$SensitiveBuildFiles = @(
  Get-ChildItem -LiteralPath $RepositoryRoot -Force -File -Recurse -ErrorAction Stop |
    Where-Object {
      $fullName = $_.FullName
      $excluded = $false
      foreach ($segment in $ExcludedScanSegments) {
        if ($fullName -like "*$segment*") {
          $excluded = $true
          break
        }
      }
      -not $excluded -and
        $AllowedNonSecretEnvFiles -notcontains $fullName -and
        $_.Name -match $SensitiveNamePattern
    }
)
if ($SensitiveBuildFiles.Count -ne 0) {
  $SensitiveFileMessage = (
    "STOP: found {0} sensitive file(s) in the release workspace; " +
    'names and contents are intentionally suppressed'
  ) -f $SensitiveBuildFiles.Count
  Write-Error $SensitiveFileMessage
  throw 'Remove sensitive files before any Docker build'
}

$ShortSha = git rev-parse --short=12 $ReleaseCommit
az acr build `
  --registry $ApprovedRegistry `
  --image "dataforge-backend:member-budget-$ShortSha" `
  --file backend/Dockerfile .
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: backend immutable image build failed'
}
az acr build `
  --registry $ApprovedRegistry `
  --image "dataforge-web:member-budget-$ShortSha" `
  --file Dockerfile web
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: web immutable image build failed'
}
```

Resolve immutable digests through the approved registry workflow. Do not deploy
floating tags.

```powershell
$BackendImage = "<registry>/dataforge-backend@sha256:<approved-digest>"
$WebImage = "<registry>/dataforge-web@sha256:<approved-digest>"
$BackendSuffix = "mb-$ShortSha"
$WebSuffix = "mb-$ShortSha"
```

## 4. Prove safe revision mode and create zero-traffic candidates

Before creating either revision, prove both apps already use `Multiple` revision
mode and route exactly 100% to the explicitly named stable revision. A
`latestRevision` route is not accepted. If either precondition fails, stop:
changing revision mode or production traffic is a separate approved operation,
not part of candidate creation.

```powershell
function Assert-ExplicitStableTraffic {
  param(
    [Parameter(Mandatory)][string]$AppName,
    [Parameter(Mandatory)][string]$StableRevision
  )

  $activeMode = az containerapp show `
    --name $AppName `
    --resource-group $ApprovedResourceGroup `
    --query properties.configuration.activeRevisionsMode -o tsv
  if ($LASTEXITCODE -ne 0 -or $activeMode -cne 'Multiple') {
    throw "STOP: $AppName is not explicitly in Multiple revision mode"
  }

  $traffic = @(
    az containerapp ingress traffic show `
      --name $AppName `
      --resource-group $ApprovedResourceGroup -o json |
      ConvertFrom-Json
  )
  if (
    $LASTEXITCODE -ne 0 -or
    $traffic.Count -eq 0 -or
    @($traffic | Where-Object { $_.latestRevision -eq $true }).Count -ne 0
  ) {
    throw "STOP: $AppName has missing or latestRevision traffic"
  }
  $positiveTraffic = @($traffic | Where-Object { [int]$_.weight -gt 0 })
  if (
    $positiveTraffic.Count -ne 1 -or
    [string]$positiveTraffic[0].revisionName -cne $StableRevision -or
    [int]$positiveTraffic[0].weight -ne 100
  ) {
    throw "STOP: $AppName is not explicitly stable=100 before candidate creation"
  }
}

Assert-ExplicitStableTraffic `
  -AppName $ApprovedBackendApp `
  -StableRevision $PreviousBackendRevision
Assert-ExplicitStableTraffic `
  -AppName $ApprovedWebApp `
  -StableRevision $PreviousWebRevision
```

Only after both assertions pass may the backend candidate be created. It uses
ACS endpoint and sender configuration only; no connection string or key is
accepted.

```powershell
$AcsEndpoint = az communication show `
  --name $ApprovedCommunicationService `
  --resource-group $ApprovedResourceGroup `
  --query hostName -o tsv
if (-not $AcsEndpoint) {
  throw 'STOP: ACS endpoint unavailable'
}
$AcsEndpoint = "https://$AcsEndpoint"
$BackendCandidateRevision = "$ApprovedBackendApp--$BackendSuffix"
$WebCandidateRevision = "$ApprovedWebApp--$WebSuffix"

az containerapp revision copy `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --revision-suffix $BackendSuffix `
  --image $BackendImage `
  --set-env-vars `
    DF_FINOPS_MEMBER_BUDGETS_ENABLED=1 `
    DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1 `
    DF_FINOPS_EMAIL_ALERTS_ENABLED=0 `
    DF_FINOPS_ACTIONS_ENABLED=0 `
    DF_ACS_EMAIL_ENDPOINT=$AcsEndpoint `
    DF_ACS_EMAIL_SENDER_ADDRESS=$ApprovedSenderAddress `
    DF_FINOPS_PORTAL_URL=$ApprovedPortalUrl
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: backend candidate creation failed'
}

az containerapp ingress traffic set `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --revision-weight "$PreviousBackendRevision=100" "$BackendCandidateRevision=0"
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: backend zero-traffic assignment failed'
}

function Assert-CandidateZeroTraffic {
  param(
    [Parameter(Mandatory)][string]$AppName,
    [Parameter(Mandatory)][string]$StableRevision,
    [Parameter(Mandatory)][string]$CandidateRevision
  )
  $traffic = @(
    az containerapp ingress traffic show `
      --name $AppName `
      --resource-group $ApprovedResourceGroup -o json |
      ConvertFrom-Json
  )
  if (
    $LASTEXITCODE -ne 0 -or
    @($traffic | Where-Object { $_.latestRevision -eq $true }).Count -ne 0
  ) {
    throw "STOP: $AppName traffic is missing or uses latestRevision"
  }
  $stable = @(
    $traffic |
      Where-Object {
        [string]$_.revisionName -ceq $StableRevision -and
        [int]$_.weight -eq 100
      }
  )
  $candidate = @(
    $traffic |
      Where-Object {
        [string]$_.revisionName -ceq $CandidateRevision -and
        [int]$_.weight -eq 0
      }
  )
  $unexpectedPositive = @(
    $traffic |
      Where-Object {
        [int]$_.weight -gt 0 -and
        [string]$_.revisionName -cne $StableRevision
      }
  )
  if (
    $stable.Count -ne 1 -or
    $candidate.Count -ne 1 -or
    $unexpectedPositive.Count -ne 0
  ) {
    throw "STOP: $AppName candidate is not candidate=0/stable=100"
  }
}

Assert-CandidateZeroTraffic `
  -AppName $ApprovedBackendApp `
  -StableRevision $PreviousBackendRevision `
  -CandidateRevision $BackendCandidateRevision
$BackendCandidateHealth = az containerapp revision show `
  --name $ApprovedBackendApp `
  --resource-group $ApprovedResourceGroup `
  --revision $BackendCandidateRevision `
  --query properties.healthState -o tsv
if ($LASTEXITCODE -ne 0 -or $BackendCandidateHealth -cne 'Healthy') {
  throw 'STOP: backend candidate is not Healthy at verified zero traffic'
}
```

Record the backend gate result, then obtain a separate explicit confirmation
before creating the web candidate:

```powershell
$ApprovedProceedWithWebCandidate = Read-Host (
  'Type CREATE_WEB_AFTER_BACKEND_ZERO_TRAFFIC to continue'
)
if (
  $ApprovedProceedWithWebCandidate -cne
  'CREATE_WEB_AFTER_BACKEND_ZERO_TRAFFIC'
) {
  throw 'STOP: web candidate was not explicitly approved'
}

Assert-ExplicitStableTraffic `
  -AppName $ApprovedWebApp `
  -StableRevision $PreviousWebRevision

az containerapp revision copy `
  --name $ApprovedWebApp `
  --resource-group $ApprovedResourceGroup `
  --revision-suffix $WebSuffix `
  --image $WebImage
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: web candidate creation failed'
}
az containerapp ingress traffic set `
  --name $ApprovedWebApp `
  --resource-group $ApprovedResourceGroup `
  --revision-weight "$PreviousWebRevision=100" "$WebCandidateRevision=0"
if ($LASTEXITCODE -ne 0) {
  throw 'STOP: web zero-traffic assignment failed'
}
Assert-CandidateZeroTraffic `
  -AppName $ApprovedWebApp `
  -StableRevision $PreviousWebRevision `
  -CandidateRevision $WebCandidateRevision
$WebCandidateHealth = az containerapp revision show `
  --name $ApprovedWebApp `
  --resource-group $ApprovedResourceGroup `
  --revision $WebCandidateRevision `
  --query properties.healthState -o tsv
if ($LASTEXITCODE -ne 0 -or $WebCandidateHealth -cne 'Healthy') {
  throw 'STOP: web candidate is not Healthy at verified zero traffic'
}
```

Do not start health/API acceptance until both post-create assertions pass.

## 5. Validate health, authorization, and UI with automatic sending off

Run through the revision-specific candidate endpoints or the approved
candidate-only routing path.

| Check | Required result |
| --- | --- |
| `GET /api/health` | `200` |
| Unauthenticated budget API | `401` |
| Active Owner/Admin budget APIs | `200` |
| Active member budget API | `403` |
| Cross-tenant member reference | rejected |
| Save approved active administrator recipient | success |
| Save another member or external recipient | rejected |
| Send one `[Test]` / `[测试]` email | accepted by ACS and received by the approved administrator |
| Reload in same browser | configuration persists |
| Sign in on second device/browser | configuration persists; no secret or credential is returned |

Desktop and mobile acceptance:

- settings entry is visible only to an authorized administrator;
- member, status, workspace, month-to-date estimated spend, USD budget,
  progress, coverage, reminder state, and edit action are legible;
- loading, partial, empty, not-configured, permission-required, unavailable,
  and conflict states remain truthful;
- no raw Entra object ID, original member identifier, ACS message ID, email
  body, connection string, or internal error body appears;
- mobile has no horizontal overflow and retains keyboard/focus behavior.

Expected local screenshot paths:

- `output/playwright/task6-member-budget-entry-desktop.png`
- `output/playwright/task6-member-budget-page-desktop.png`
- `output/playwright/task6-member-budget-page-mobile.png`

These screenshots are local UI evidence only. They do not prove live Azure,
email delivery, authorization, or persistence across devices.

## 6. Manually reconcile one `$190 / $200` scenario

Use only controlled candidate data in the approved tenant.

1. Create a `$200` UTC calendar-month budget with thresholds `80/95/100`.
2. Query the same tenant, authorized workspaces, actor reference, and UTC month
   from reconciled `df_finops.request_event`.
3. Sum only request facts whose estimated cost is priced/estimated/partial.
4. Count all relevant requests, including unpriced/unavailable rows.
5. Confirm the portal amount equals the independently calculated `$190`.
6. Confirm one or more unpriced rows reduce pricing coverage instead of adding
   `$0`.

Record the query method, row counts, calculated amount, coverage, price-card
revision lineage, and UTC window. Do not record raw identity, request payloads,
provider response IDs, or member email.

## 7. Create the 15-minute Container Apps Job last

Create or update the scheduled job only after Sections 1-6 pass. Keep automatic
alerts disabled at creation time.

```powershell
az containerapp job create `
  --name $ApprovedJob `
  --resource-group $ApprovedResourceGroup `
  --environment $ApprovedContainerAppsEnvironment `
  --trigger-type Schedule `
  --cron-expression '*/15 * * * *' `
  --replica-timeout 900 `
  --replica-retry-limit 1 `
  --image $BackendImage `
  --command python `
  --args -m backend.finops.member_budget_refresh `
  --env-vars `
    DF_FINOPS_MEMBER_BUDGETS_ENABLED=1 `
    DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1 `
    DF_FINOPS_EMAIL_ALERTS_ENABLED=0 `
    DF_FINOPS_ACTIONS_ENABLED=0 `
    DF_ACS_EMAIL_ENDPOINT=$AcsEndpoint `
    DF_ACS_EMAIL_SENDER_ADDRESS=$ApprovedSenderAddress `
    DF_FINOPS_PORTAL_URL=$ApprovedPortalUrl
```

Configure the job's system-assigned managed identity, minimum ACS send role,
Azure SQL access, and the same non-secret settings through the approved
infrastructure workflow. Do not copy credentials into job variables.

## 8. Controlled automatic-alert test, then disable again

This section requires a second explicit human approval while the candidate and
job remain isolated from production traffic.

1. Confirm the `$200` budget, `80/95/100` thresholds, `$190` priced spend, and
   incomplete-coverage scenario.
2. Temporarily set `DF_FINOPS_EMAIL_ALERTS_ENABLED=1` on the isolated job only.
3. Start one execution:

   ```powershell
   az containerapp job start `
     --name $ApprovedJob `
     --resource-group $ApprovedResourceGroup
   ```

4. Confirm exactly one 95% email and one durable alert row.
5. Start the job again and confirm no duplicate email or alert.
6. In a separate controlled period/budget, jump directly above 100%; confirm
   only the highest threshold sends and lower crossed thresholds are recorded
   as suppressed.
7. Immediately restore `DF_FINOPS_EMAIL_ALERTS_ENABLED=0` and verify it on the
   job configuration.

Automatic reminders remain `0` after candidate acceptance. Enabling them in
production requires a separate, explicit approval.

## 9. Redacted logs and evidence

Use a bounded UTC window around candidate validation. Record only:

- candidate revision and job names;
- immutable image digests;
- health and HTTP status categories;
- ACS accepted/received timestamps;
- alert ID only if it is an opaque DataForge reference;
- duplicate count;
- safe error category counts;
- role-assignment outcome;
- rollback revision names.

The retained log excerpt must contain no member email, raw identity/object ID,
subscription/principal/resource ID, ACS message ID, endpoint path containing
identity, request/response body, token, credential, connection string, or
internal exception text. Record zero/non-zero counts for:

- authentication/authorization failures outside expected negative tests;
- `audit_persistence_required`;
- SQL persistence failures;
- ACS `permission_required` / `provider_unavailable`;
- duplicate alert delivery;
- traceback/unhandled server error;
- secret-like markers.

## 10. Rollback and production gate

Rollback triggers include failed health, authorization leakage, incorrect
tenant/workspace scoping, incorrect `$190 / $200` reconciliation, unpriced rows
treated as zero, duplicate email, unsafe error/log content, ACS permission
failure, or UI regression.

Rollback order:

1. Set `DF_FINOPS_EMAIL_ALERTS_ENABLED=0` and stop/disable the scheduled job.
2. Route web traffic back to `$PreviousWebRevision`.
3. Route backend traffic back to `$PreviousBackendRevision`.
4. Recheck health, authorization, and critical logs.
5. Retain additive SQL tables and candidate revisions for investigation; do not
   delete evidence or drop tables.

Production promotion requires explicit human approval after every candidate
item is PASS. Promote backend before web, recheck health and critical logs, and
enable the scheduled job last. `DF_FINOPS_ACTIONS_ENABLED` remains `0`.

After the production approver authorizes promotion, move backend traffic first
and stop unless health, authorization, and the bounded critical-log query pass.
Only then request the separate confirmation token
`PROMOTE_WEB_AFTER_BACKEND_HEALTHY`; without that exact confirmation, web
traffic remains on `$PreviousWebRevision`. After web promotion, repeat the same
checks before enabling the scheduled job. Automatic alerts remain a separate
approval even when the job exists.
