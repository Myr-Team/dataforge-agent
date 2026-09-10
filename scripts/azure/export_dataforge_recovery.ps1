[CmdletBinding()]
param(
    [string]$SubscriptionName,
    [string]$OutputPath = "output/recovery/azure-current-manifest.json"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-AzJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $json = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure inventory query failed."
    }
    if (-not $json) {
        return $null
    }
    return $json | ConvertFrom-Json
}

function Add-Rows {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$Target,
        [AllowNull()]$Rows
    )

    if ($null -eq $Rows) { return }
    foreach ($row in @($Rows)) {
        [void]$Target.Add($row)
    }
}

if (-not $SubscriptionName) {
    $SubscriptionName = (& az account show --query name --output tsv).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $SubscriptionName) {
        throw "An enabled Azure CLI subscription is required."
    }
}

$subscriptionState = (& az account list --query "[?name=='$SubscriptionName'].state | [0]" --output tsv).Trim()
if ($LASTEXITCODE -ne 0 -or $subscriptionState -ne "Enabled") {
    throw "The selected Azure subscription is not enabled."
}

$resourceGroupNames = @("rg-dataforge-dev", "Agent-Demo-Fuzh")
$snapshot = [ordered]@{
    generated_at = [DateTimeOffset]::Now.ToString("o")
    subscription_display_name = $SubscriptionName
    resource_groups = [System.Collections.ArrayList]::new()
    container_apps = [System.Collections.ArrayList]::new()
    jobs = [System.Collections.ArrayList]::new()
    sql_databases = [System.Collections.ArrayList]::new()
    api_management = [System.Collections.ArrayList]::new()
    search_services = [System.Collections.ArrayList]::new()
    foundry = [System.Collections.ArrayList]::new()
    supporting_resources = [System.Collections.ArrayList]::new()
}

foreach ($resourceGroupName in $resourceGroupNames) {
    $exists = (& az group exists --subscription $SubscriptionName --name $resourceGroupName --output tsv).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Resource group existence check failed." }
    if ($exists -ne "true") { continue }

    $group = Invoke-AzJson @(
        "group", "show",
        "--subscription", $SubscriptionName,
        "--name", $resourceGroupName,
        "--query", "{name:name,location:location}"
    )
    [void]$snapshot.resource_groups.Add($group)

    Add-Rows $snapshot.supporting_resources (Invoke-AzJson @(
        "resource", "list",
        "--subscription", $SubscriptionName,
        "--resource-group", $resourceGroupName,
        "--query", "[].{name:name,type:type,resource_group:resourceGroup,location:location}"
    ))

    Add-Rows $snapshot.container_apps (Invoke-AzJson @(
        "containerapp", "list",
        "--subscription", $SubscriptionName,
        "--resource-group", $resourceGroupName,
        "--query", "[].{name:name,resource_group:resourceGroup,image:properties.template.containers[0].image,min_replicas:properties.template.scale.minReplicas,max_replicas:properties.template.scale.maxReplicas,ingress_external:properties.configuration.ingress.external,target_port:properties.configuration.ingress.targetPort,environment:properties.template.containers[0].env[].{name:name,secret_ref:secretRef}}"
    ))

    Add-Rows $snapshot.jobs (Invoke-AzJson @(
        "containerapp", "job", "list",
        "--subscription", $SubscriptionName,
        "--resource-group", $resourceGroupName,
        "--query", "[].{name:name,resource_group:resourceGroup,trigger_type:properties.configuration.triggerType,cron_expression:properties.configuration.scheduleTriggerConfig.cronExpression,image:properties.template.containers[0].image,environment:properties.template.containers[0].env[].{name:name,secret_ref:secretRef}}"
    ))

    $sqlServers = Invoke-AzJson @(
        "sql", "server", "list",
        "--subscription", $SubscriptionName,
        "--resource-group", $resourceGroupName,
        "--query", "[].name"
    )
    foreach ($sqlServer in @($sqlServers)) {
        Add-Rows $snapshot.sql_databases (Invoke-AzJson @(
            "sql", "db", "list",
            "--subscription", $SubscriptionName,
            "--resource-group", $resourceGroupName,
            "--server", ([string]$sqlServer),
            "--query", "[?name!='master'].{name:name,resource_group:resourceGroup,server:'$sqlServer',sku:sku.name,status:status}"
        ))
    }

    $apimServices = Invoke-AzJson @(
        "apim", "list",
        "--subscription", $SubscriptionName,
        "--resource-group", $resourceGroupName,
        "--query", "[].{name:name,resource_group:resourceGroup,sku:sku.name,capacity:sku.capacity}"
    )
    foreach ($apim in @($apimServices)) {
        $apis = Invoke-AzJson @(
            "apim", "api", "list",
            "--subscription", $SubscriptionName,
            "--resource-group", $resourceGroupName,
            "--service-name", ([string]$apim.name),
            "--query", "[].{name:name,path:path}"
        )
        [void]$snapshot.api_management.Add([ordered]@{
            name = [string]$apim.name
            resource_group = [string]$apim.resource_group
            sku = [string]$apim.sku
            capacity = $apim.capacity
            apis = @($apis)
        })
    }

    Add-Rows $snapshot.search_services (Invoke-AzJson @(
        "search", "service", "list",
        "--subscription", $SubscriptionName,
        "--resource-group", $resourceGroupName,
        "--query", "[].{name:name,resource_group:resourceGroup,sku:sku.name,replica_count:replicaCount,partition_count:partitionCount}"
    ))

    $foundryAccounts = Invoke-AzJson @(
        "cognitiveservices", "account", "list",
        "--subscription", $SubscriptionName,
        "--resource-group", $resourceGroupName,
        "--query", "[?kind=='AIServices'].{name:name,resource_group:resourceGroup}"
    )
    foreach ($foundryAccount in @($foundryAccounts)) {
        $deployments = Invoke-AzJson @(
            "cognitiveservices", "account", "deployment", "list",
            "--subscription", $SubscriptionName,
            "--resource-group", $resourceGroupName,
            "--name", ([string]$foundryAccount.name),
            "--query", "[].name"
        )
        [void]$snapshot.foundry.Add([ordered]@{
            name = [string]$foundryAccount.name
            resource_group = [string]$foundryAccount.resource_group
            deployments = @($deployments)
        })
    }
}

$temporaryInput = New-TemporaryFile
try {
    $snapshot | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temporaryInput.FullName -Encoding utf8
    $repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $manifestTool = Join-Path $PSScriptRoot "dataforge_recovery_manifest.py"
    $resolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
        $OutputPath
    } else {
        Join-Path $repositoryRoot $OutputPath
    }
    & python $manifestTool $temporaryInput.FullName $resolvedOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Recovery manifest safety projection failed."
    }
    Write-Output $resolvedOutput
}
finally {
    Remove-Item -LiteralPath $temporaryInput.FullName -Force -ErrorAction SilentlyContinue
}
