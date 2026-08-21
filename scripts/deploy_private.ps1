param(
    [string]$EnvironmentName = "maf-poc-private",
    [string]$SubscriptionId = "87e1a785-896b-4eb4-a214-47f67995133e",
    [string]$TenantId = "c214aaa8-7a43-441a-b501-f942c96f54a8",
    [string]$ResourceGroup = "rg-maf-poc-private-uk",
    [string]$Location = "uksouth",
    [string]$ModelName = "gpt-5.6-luna",
    [string]$ModelDeploymentName = "gpt-5.6-luna",
    [string]$ModelVersion = "2026-07-09",
    [string]$ModelSkuName = "GlobalStandard",
    [int]$ModelCapacity = 500,
    [switch]$CleanupTemporaryPublicDeployment,
    [string]$TemporaryEnvironmentName = "maf-poc-dev",
    [string]$TemporaryProjectEndpoint = "https://foundry-uk-public.services.ai.azure.com/api/projects/proj-default",
    [string]$TemporaryResourceGroup = "rg-foundy-uk",
    [string]$TemporaryCosmosAccountName = "cosmos-dck3gj57hdvaw",
    [string]$TemporaryPolicyExemptionName = "maf-poc-cosmos-hosted-agent"
)

$ErrorActionPreference = "Stop"
$env:AZD_NON_INTERACTIVE = "true"

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][scriptblock]$Command,
        [Parameter(Mandatory)][string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Get-AzdValue {
    param([Parameter(Mandatory)][string]$Name)

    $value = azd env get-value $Name --environment $EnvironmentName
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "Missing azd environment value '$Name' in '$EnvironmentName'."
    }
    return $value.Trim()
}

function Wait-ManagedNetwork {
    param(
        [Parameter(Mandatory)][string]$AccountName,
        [int]$TimeoutMinutes = 30
    )

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    do {
        $managedNetwork = az cognitiveservices account managed-network show `
            --name $AccountName `
            --resource-group $ResourceGroup `
            --output json 2>$null | ConvertFrom-Json
        $networkActive = (
            $managedNetwork.properties.managedNetwork.status.status -eq "Active"
        )
        $requiredRules = @("cosmos-sql-rule", "search-service-rule")
        $activeRules = @(
            foreach ($ruleName in $requiredRules) {
                $ruleProperty = (
                    $managedNetwork.properties.managedNetwork.outboundRules.
                        PSObject.Properties[$ruleName]
                )
                if ($ruleProperty.Value.status -eq "Active") {
                    $ruleName
                }
            }
        )
        if ($networkActive -and $activeRules.Count -eq $requiredRules.Count) {
            return
        }

        Write-Output "Waiting for Foundry managed network and private endpoint rules..."
        Start-Sleep -Seconds 30
    } while ((Get-Date) -lt $deadline)

    throw "Foundry managed network did not become active within $TimeoutMinutes minutes."
}

Assert-Command "az"
Assert-Command "azd"

Invoke-Checked { az account set --subscription $SubscriptionId } `
    "Unable to select Azure subscription '$SubscriptionId'."
$account = az account show --output json | ConvertFrom-Json
if (
    $LASTEXITCODE -ne 0 -or
    $account.id -ne $SubscriptionId -or
    $account.tenantId -ne $TenantId
) {
    throw "Run 'az login --tenant $TenantId' and retry."
}

Invoke-Checked { azd auth login --check-status } `
    "Run 'azd auth login --tenant-id $TenantId' and retry."
Invoke-Checked { az bicep build --file "infra\main.bicep" --stdout | Out-Null } `
    "Bicep validation failed."

$existingDeployment = $null
if ((az group exists --name $ResourceGroup) -eq "true") {
    $existingAccount = az cognitiveservices account list `
        --resource-group $ResourceGroup `
        --query "[?kind=='AIServices'] | [0].name" `
        --output tsv
    if ($existingAccount) {
        $existingDeployment = az cognitiveservices account deployment show `
            --name $existingAccount `
            --resource-group $ResourceGroup `
            --deployment-name $ModelDeploymentName `
            --output json 2>$null | ConvertFrom-Json
    }
}

$selectedCapacity = $ModelCapacity
if (-not $existingDeployment) {
    $quotaName = "OpenAI.$ModelSkuName.$ModelName"
    $usage = az cognitiveservices usage list `
        --location $Location `
        --query "[?name.value=='$quotaName'] | [0]" `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $usage) {
        throw "No quota record found for $ModelName ($ModelSkuName) in $Location."
    }

    $availableCapacity = [math]::Floor(
        [double]$usage.limit - [double]$usage.currentValue
    )
    if ($availableCapacity -lt 1) {
        throw (
            "No $ModelName $ModelSkuName capacity remains in $Location " +
            "($($usage.currentValue)/$($usage.limit) in use). Request quota " +
            "or free capacity before deployment."
        )
    }
    if ($selectedCapacity -gt $availableCapacity) {
        Write-Warning (
            "Requested model capacity $selectedCapacity exceeds the remaining " +
            "capacity $availableCapacity. Using $availableCapacity."
        )
        $selectedCapacity = $availableCapacity
    }
}

$environments = @(azd env list --output json | ConvertFrom-Json)
if ($environments.Name -contains $EnvironmentName) {
    Invoke-Checked { azd env select $EnvironmentName } `
        "Unable to select azd environment '$EnvironmentName'."
} else {
    Invoke-Checked {
        azd env new $EnvironmentName `
            --subscription $SubscriptionId `
            --location $Location `
            --no-prompt
    } "Unable to create azd environment '$EnvironmentName'."
}

$principalId = az ad signed-in-user show --query id --output tsv
if ($LASTEXITCODE -ne 0 -or -not $principalId) {
    throw "Unable to resolve the signed-in user's Microsoft Entra object ID."
}

$settings = @{
    AZURE_SUBSCRIPTION_ID = $SubscriptionId
    AZURE_TENANT_ID = $TenantId
    AZURE_RESOURCE_GROUP = $ResourceGroup
    AZURE_LOCATION = $Location
    AZURE_PRINCIPAL_ID = $principalId
    AZURE_AI_PROJECT_NAME = "maf-poc-project"
    AZURE_AI_MODEL_DEPLOYMENT_NAME = $ModelDeploymentName
    AZURE_AI_MODEL_NAME = $ModelName
    AZURE_AI_MODEL_VERSION = $ModelVersion
    AZURE_AI_MODEL_SKU_NAME = $ModelSkuName
    AZURE_AI_MODEL_CAPACITY = $selectedCapacity
    DEPENDENCY_STARTUP_CHECKS = "false"
}
foreach ($setting in $settings.GetEnumerator()) {
    Invoke-Checked {
        azd env set $setting.Key ([string]$setting.Value) `
            --environment $EnvironmentName
    } "Unable to set azd environment value '$($setting.Key)'."
}

Invoke-Checked { azd provision --environment $EnvironmentName --no-prompt } `
    "Private infrastructure provisioning failed."

$foundryAccount = Get-AzdValue "AZURE_AI_ACCOUNT_NAME"
$foundryProject = Get-AzdValue "AZURE_AI_PROJECT_NAME"
$connectionsUri = (
    "https://management.azure.com/subscriptions/$SubscriptionId/" +
    "resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/" +
    "accounts/$foundryAccount/projects/$foundryProject/" +
    "connections?api-version=2025-04-01-preview"
)
$connections = az rest --method get --url $connectionsUri --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Foundry project connections."
}
$applicationInsightsResourceId = (
    $connections.value |
        Where-Object { $_.properties.category -eq "AppInsights" } |
        Select-Object -First 1 -ExpandProperty properties
).target
if (-not $applicationInsightsResourceId) {
    throw "The Foundry project does not expose an Application Insights connection."
}
$applicationInsightsConnectionString = az resource show `
    --ids $applicationInsightsResourceId `
    --api-version "2020-02-02" `
    --query properties.ConnectionString `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $applicationInsightsConnectionString) {
    throw "Unable to resolve the project Application Insights connection string."
}
Invoke-Checked {
    azd env set APPLICATIONINSIGHTS_CONNECTION_STRING `
        $applicationInsightsConnectionString `
        --environment $EnvironmentName
} "Unable to configure hosted Application Insights export."

Invoke-Checked {
    az cognitiveservices account managed-network provision-network `
        --name $foundryAccount `
        --resource-group $ResourceGroup `
        --output none
} "Unable to start Foundry managed-network provisioning."
Wait-ManagedNetwork $foundryAccount

Invoke-Checked { azd deploy --environment $EnvironmentName --no-prompt } `
    "Bootstrap hosted-agent deployment failed."
Invoke-Checked {
    & "$PSScriptRoot\assign_hosted_agent_roles.ps1" `
        -EnvironmentName $EnvironmentName `
        -ResourceGroup $ResourceGroup
} "Hosted-agent role assignment failed."

Invoke-Checked {
    azd env set DEPENDENCY_STARTUP_CHECKS true `
        --environment $EnvironmentName
} "Unable to enable hosted dependency startup checks."

$deployed = $false
for ($attempt = 1; $attempt -le 5 -and -not $deployed; $attempt++) {
    azd deploy --environment $EnvironmentName --no-prompt
    if ($LASTEXITCODE -eq 0) {
        $deployed = $true
        break
    }
    if ($attempt -lt 5) {
        Write-Warning "Deployment attempt $attempt failed; waiting for RBAC propagation."
        Start-Sleep -Seconds 30
    }
}
if (-not $deployed) {
    throw "Hosted-agent deployment failed after RBAC propagation retries."
}

Invoke-Checked {
    & "$PSScriptRoot\validate_private_deployment.ps1" `
        -EnvironmentName $EnvironmentName
} "Private deployment validation failed."

if ($CleanupTemporaryPublicDeployment) {
    Invoke-Checked {
        & "$PSScriptRoot\cleanup_temporary_public.ps1" `
            -Execute `
            -EnvironmentName $TemporaryEnvironmentName `
            -ProjectEndpoint $TemporaryProjectEndpoint `
            -ResourceGroup $TemporaryResourceGroup `
            -CosmosAccountName $TemporaryCosmosAccountName `
            -PolicyExemptionName $TemporaryPolicyExemptionName
    } "Temporary public deployment cleanup failed."

    Invoke-Checked {
        azd deploy --environment $EnvironmentName --no-prompt
    } "Unable to refresh private agent metadata after public cleanup."
    Invoke-Checked {
        & "$PSScriptRoot\assign_hosted_agent_roles.ps1" `
            -EnvironmentName $EnvironmentName `
            -ResourceGroup $ResourceGroup
    } "Unable to refresh hosted-agent roles after public cleanup."
    Invoke-Checked {
        azd deploy --environment $EnvironmentName --no-prompt
    } "Unable to restart the private agent after refreshing roles."
    Invoke-Checked {
        & "$PSScriptRoot\validate_private_deployment.ps1" `
            -EnvironmentName $EnvironmentName
    } "Private deployment validation failed after public cleanup."
}

Write-Output "Private Foundry agent deployment completed successfully."
