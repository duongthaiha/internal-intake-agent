param(
    [string]$EnvironmentName = "maf-poc-private",
    [string]$AgentName = "maf-poc-agent",
    [string]$ExpectedSource = "maf-poc",
    [string]$Question = "Which model deployment does this POC use? Cite the source."
)

$ErrorActionPreference = "Stop"

function Get-AzdValue {
    param([Parameter(Mandatory)][string]$Name)

    $value = azd env get-value $Name --environment $EnvironmentName
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "Missing azd environment value '$Name' in '$EnvironmentName'."
    }
    return $value.Trim()
}

$resourceGroup = Get-AzdValue "AZURE_RESOURCE_GROUP"
$foundryAccount = Get-AzdValue "AZURE_AI_ACCOUNT_NAME"
$cosmosAccount = Get-AzdValue "AZURE_COSMOS_ACCOUNT_NAME"
$searchService = Get-AzdValue "AZURE_SEARCH_SERVICE_NAME"
$modelDeployment = Get-AzdValue "AZURE_AI_MODEL_DEPLOYMENT_NAME"

$cosmos = az cosmosdb show `
    --name $cosmosAccount `
    --resource-group $resourceGroup `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Cosmos DB account '$cosmosAccount'."
}
if ($cosmos.publicNetworkAccess -ne "Disabled" -or -not $cosmos.disableLocalAuth) {
    throw "Cosmos DB must have public access disabled and local authentication disabled."
}

$search = az search service show `
    --name $searchService `
    --resource-group $resourceGroup `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Azure AI Search service '$searchService'."
}
if ($search.publicNetworkAccess.ToLowerInvariant() -ne "disabled" -or -not $search.disableLocalAuth) {
    throw "Azure AI Search must have public access disabled and local authentication disabled."
}

$managedNetwork = az cognitiveservices account managed-network show `
    --name $foundryAccount `
    --resource-group $resourceGroup `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the Foundry managed network."
}
if ($managedNetwork.properties.managedNetwork.isolationMode -ne "AllowInternetOutbound") {
    throw "The Foundry managed network is not using AllowInternetOutbound."
}

foreach ($ruleName in @("cosmos-sql-rule", "search-service-rule")) {
    $ruleProperty = (
        $managedNetwork.properties.managedNetwork.outboundRules.
            PSObject.Properties[$ruleName]
    )
    if (-not $ruleProperty) {
        throw "Managed-network outbound rule '$ruleName' was not found."
    }
    if ($ruleProperty.Value.status -ne "Active") {
        throw "Managed-network outbound rule '$ruleName' is '$($ruleProperty.Value.status)', not Active."
    }
}

$agent = azd ai agent show $AgentName `
    --environment $EnvironmentName `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $agent) {
    throw "Hosted agent '$AgentName' is not available."
}

$response = $null
for ($attempt = 1; $attempt -le 3; $attempt++) {
    $response = azd ai agent invoke $AgentName $Question `
        --environment $EnvironmentName `
        --new-session `
        --timeout 600 `
        --output default
    if ($LASTEXITCODE -eq 0) {
        break
    }
    if ($attempt -lt 3) {
        Write-Warning "Hosted agent smoke test attempt $attempt failed; retrying."
        Start-Sleep -Seconds 30
    }
}
if ($LASTEXITCODE -ne 0 -or -not $response) {
    throw "Hosted agent smoke test failed."
}
$responseText = $response -join [Environment]::NewLine
if (
    $responseText -notmatch [regex]::Escape($modelDeployment) -or
    $responseText -notmatch [regex]::Escape($ExpectedSource)
) {
    throw "The hosted response was not grounded with '$modelDeployment' and '$ExpectedSource'."
}

Write-Output "Private deployment validation passed."
Write-Output "Successful agent startup also verifies Search indexing and the Cosmos write/read/delete connectivity check."
