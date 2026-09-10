[CmdletBinding()]
param(
    [string]$ManifestPath = "docs/operations/azure-recovery/current-manifest.json",
    [switch]$Execute,
    [switch]$IncludeScheduledJobs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$resolvedManifestPath = if ([System.IO.Path]::IsPathRooted($ManifestPath)) {
    $ManifestPath
} else {
    Join-Path $repositoryRoot $ManifestPath
}
if (-not (Test-Path -LiteralPath $resolvedManifestPath -PathType Leaf)) {
    throw "Recovery manifest not found."
}

$manifest = Get-Content -LiteralPath $resolvedManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1) {
    throw "Unsupported recovery manifest schema."
}
$subscriptionName = [string]$manifest.subscription_display_name
if (-not $subscriptionName) {
    throw "Recovery manifest is missing its subscription display name."
}

$applicationOrder = @(
    "ca-dataforge-redis",
    "ca-dataforge-mcp",
    "ca-dataforge-backend",
    "ca-dataforge-web"
)
$appsByName = @{}
foreach ($app in @($manifest.container_apps)) {
    $appsByName[[string]$app.name] = $app
}

$plan = [System.Collections.ArrayList]::new()
foreach ($appName in $applicationOrder) {
    if (-not $appsByName.ContainsKey($appName)) {
        throw "Required runtime application is missing from the manifest."
    }
    $app = $appsByName[$appName]
    [void]$plan.Add([ordered]@{
        action = "start_container_app"
        name = $appName
        resource_group = [string]$app.resource_group
        min_replicas = [int]$app.min_replicas
        max_replicas = [int]$app.max_replicas
    })
}

if ($IncludeScheduledJobs) {
    foreach ($job in @($manifest.jobs | Where-Object { $_.trigger_type -eq "Schedule" } | Sort-Object name)) {
        [void]$plan.Add([ordered]@{
            action = "restore_scheduled_job"
            name = [string]$job.name
            resource_group = [string]$job.resource_group
            cron_expression = [string]$job.cron_expression
        })
    }
}

if (-not $Execute) {
    [ordered]@{
        mode = "dry_run"
        subscription_display_name = $subscriptionName
        actions = $plan
    } | ConvertTo-Json -Depth 8
    return
}

$subscriptionState = (& az account list --query "[?name=='$subscriptionName'].state | [0]" --output tsv).Trim()
if ($LASTEXITCODE -ne 0 -or $subscriptionState -ne "Enabled") {
    throw "The recovery subscription is not enabled."
}
$subscriptionId = (& az account show --subscription $subscriptionName --query id --output tsv).Trim()
if ($LASTEXITCODE -ne 0 -or $subscriptionId -notmatch "^[0-9a-fA-F-]{36}$") {
    throw "The recovery subscription identifier could not be resolved."
}

# Complete every existence check before the first Azure mutation.
foreach ($action in $plan) {
    if ($action.action -eq "start_container_app") {
        & az containerapp show `
            --subscription $subscriptionName `
            --resource-group $action.resource_group `
            --name $action.name `
            --query name `
            --output none `
            --only-show-errors
    } else {
        & az containerapp job show `
            --subscription $subscriptionName `
            --resource-group $action.resource_group `
            --name $action.name `
            --query name `
            --output none `
            --only-show-errors
    }
    if ($LASTEXITCODE -ne 0) {
        throw "A required recovery resource is missing; no runtime changes were made."
    }
}

foreach ($action in $plan) {
    if ($action.action -eq "start_container_app") {
        $encodedGroup = [Uri]::EscapeDataString([string]$action.resource_group)
        $encodedName = [Uri]::EscapeDataString([string]$action.name)
        $startUrl = "https://management.azure.com/subscriptions/$subscriptionId/resourceGroups/$encodedGroup/providers/Microsoft.App/containerApps/$encodedName/start?api-version=2025-07-01"
        & az rest `
            --method post `
            --url $startUrl `
            --only-show-errors `
            --output none
        if ($LASTEXITCODE -ne 0) { throw "Container application start failed." }

        & az containerapp update `
            --subscription $subscriptionName `
            --resource-group $action.resource_group `
            --name $action.name `
            --min-replicas $action.min_replicas `
            --max-replicas $action.max_replicas `
            --only-show-errors `
            --output none
        if ($LASTEXITCODE -ne 0) { throw "Container application scale restore failed." }
        continue
    }

    & az containerapp job update `
        --subscription $subscriptionName `
        --resource-group $action.resource_group `
        --name $action.name `
        --trigger-type Schedule `
        --cron-expression $action.cron_expression `
        --only-show-errors `
        --output none
    if ($LASTEXITCODE -ne 0) { throw "Scheduled job restore failed." }
}

[ordered]@{
    mode = "executed"
    completed_actions = @($plan).Count
} | ConvertTo-Json
