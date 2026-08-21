param(
    [switch]$Execute,
    [string]$EnvironmentName = "maf-poc-dev",
    [string]$AgentName = "maf-poc-agent",
    [string]$ProjectEndpoint = "https://foundry-uk-public.services.ai.azure.com/api/projects/proj-default",
    [string]$ResourceGroup = "rg-foundy-uk",
    [string]$CosmosAccountName = "cosmos-dck3gj57hdvaw",
    [string]$PolicyExemptionName = "maf-poc-cosmos-hosted-agent"
)

$ErrorActionPreference = "Stop"

if (-not $Execute) {
    Write-Output "No changes made. Re-run with -Execute after the private deployment passes validation."
    Write-Output "Targets: agent '$AgentName', exemption '$PolicyExemptionName', Cosmos '$CosmosAccountName'."
    exit 0
}

$agentToken = az account get-access-token `
    --resource "https://ai.azure.com" `
    --query accessToken `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $agentToken) {
    throw "Unable to acquire a Foundry API token."
}

$agentHeaders = @{ Authorization = "Bearer $agentToken" }
$agentUri = (
    "$($ProjectEndpoint.TrimEnd('/'))/agents/${AgentName}" +
    "?api-version=v1&force=true"
)
try {
    Invoke-RestMethod `
        -Method Delete `
        -Uri $agentUri `
        -Headers $agentHeaders | Out-Null
} catch {
    if ([int]$_.Exception.Response.StatusCode -ne 404) {
        throw
    }
}

$exemption = az policy exemption show `
    --name $PolicyExemptionName `
    --resource-group $ResourceGroup `
    --output json 2>$null
if ($LASTEXITCODE -eq 0 -and $exemption) {
    az policy exemption delete `
        --name $PolicyExemptionName `
        --resource-group $ResourceGroup `
        --output none
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to delete policy exemption '$PolicyExemptionName'."
    }
}

$subscriptionId = az account show --query id --output tsv
$token = az account get-access-token `
    --resource "https://management.azure.com" `
    --query accessToken `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $subscriptionId -or -not $token) {
    throw "Unable to acquire an Azure Resource Manager token."
}

$headers = @{ Authorization = "Bearer $token" }
$body = @{
    properties = @{
        publicNetworkAccess = "Disabled"
        ipRules = @()
        networkAclBypass = "None"
        networkAclBypassResourceIds = @()
    }
} | ConvertTo-Json -Depth 5
$uri = (
    "https://management.azure.com/subscriptions/$subscriptionId/" +
    "resourceGroups/$ResourceGroup/providers/Microsoft.DocumentDB/" +
    "databaseAccounts/${CosmosAccountName}?api-version=2024-11-15"
)
Invoke-RestMethod `
    -Method Patch `
    -Uri $uri `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body | Out-Null

Write-Output "Temporary public agent and exemption were removed."
Write-Output "Cosmos DB '$CosmosAccountName' now has public network access disabled and no IP allowlist."
