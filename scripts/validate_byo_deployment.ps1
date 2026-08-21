<#
.SYNOPSIS
    Validates the BYO-VNet (dual inbound path) Foundry hosted-agent deployment.

.DESCRIPTION
    Replaces validate_private_deployment.ps1's managed-network checks with the
    checks that actually apply to the current infra/main.bicep BYO-VNet setup:
      - Foundry account public network access is Enabled (dual inbound path)
        with networkAcls.defaultAction Deny and exactly the expected
        allowlisted narrow client IPv4 CIDR.
      - Foundry networkInjections has the "agent" scenario pointed at the
        expected agent subnet with useMicrosoftManagedNetwork=false.
      - The agent subnet has the Microsoft.App/environments delegation.
      - Cosmos DB / Azure AI Search / Storage / Container Registry public
        access posture matches infra/UPSTREAM.md.
      - Every private endpoint connection is Approved (count + status).
      - Every expected private DNS zone has a Succeeded VNet link.
      - The hosted agent is reachable and its response is grounded (includes
        the model deployment name and the expected knowledge source), which
        also proves the Cosmos DB and Search startup checks passed.
    No `az cognitiveservices account managed-network *` calls are made.
#>
param(
    [string]$EnvironmentName = "maf-poc-byo",
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

function Get-AzdValueOrDefault {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$Default = ""
    )

    $value = azd env get-value $Name --environment $EnvironmentName 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

$resourceGroup = Get-AzdValue "AZURE_RESOURCE_GROUP"
$foundryAccount = Get-AzdValue "AZURE_AI_ACCOUNT_NAME"
$cosmosAccount = Get-AzdValue "AZURE_COSMOS_ACCOUNT_NAME"
$searchService = Get-AzdValue "AZURE_SEARCH_SERVICE_NAME"
$storageAccount = Get-AzdValue "AZURE_STORAGE_ACCOUNT_NAME"
$modelDeployment = Get-AzdValue "AZURE_AI_MODEL_DEPLOYMENT_NAME"
$vnetName = Get-AzdValue "AZURE_VIRTUAL_NETWORK_NAME"
$agentSubnetId = Get-AzdValue "AZURE_AGENT_SUBNET_ID"
$allowedClientIpCidr = Get-AzdValue "AZURE_ALLOWED_CLIENT_IP_CIDR"
$allowedClientIp = $allowedClientIpCidr -replace '/32$', ''
$acrName = Get-AzdValueOrDefault "AZURE_CONTAINER_REGISTRY_NAME"
$acrDeveloperIpCidr = Get-AzdValueOrDefault "AZURE_ACR_DEVELOPER_IP_CIDR"

# ---------------------------------------------------------------------------
# Foundry account: dual inbound path posture
# ---------------------------------------------------------------------------
$foundry = az cognitiveservices account show `
    --name $foundryAccount `
    --resource-group $resourceGroup `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Foundry account '$foundryAccount'."
}
if ($foundry.properties.publicNetworkAccess -ne "Enabled") {
    throw "Foundry account '$foundryAccount' publicNetworkAccess must be 'Enabled' (dual inbound path), found '$($foundry.properties.publicNetworkAccess)'."
}
if ($foundry.properties.networkAcls.defaultAction -ne "Deny") {
    throw "Foundry account '$foundryAccount' networkAcls.defaultAction must be 'Deny', found '$($foundry.properties.networkAcls.defaultAction)'."
}
$ipRules = @($foundry.properties.networkAcls.ipRules | ForEach-Object { $_.value })
if ($ipRules.Count -ne 1 -or $ipRules[0] -ne $allowedClientIp) {
    throw (
        "Foundry account '$foundryAccount' networkAcls.ipRules must contain exactly one entry " +
        "equal to '$allowedClientIp' (the API representation of '$allowedClientIpCidr'), found: $($ipRules -join ', ')."
    )
}

$networkInjections = @($foundry.properties.networkInjections)
$agentInjection = $networkInjections | Where-Object { $_.scenario -eq "agent" } | Select-Object -First 1
if (-not $agentInjection) {
    throw "Foundry account '$foundryAccount' has no networkInjections entry with scenario 'agent'."
}
if ($agentInjection.useMicrosoftManagedNetwork -ne $false) {
    throw "Foundry account '$foundryAccount' agent networkInjections.useMicrosoftManagedNetwork must be false."
}
if ($agentInjection.subnetArmId -ne $agentSubnetId) {
    throw (
        "Foundry account '$foundryAccount' agent networkInjections.subnetArmId must equal " +
        "'$agentSubnetId', found '$($agentInjection.subnetArmId)'."
    )
}

# ---------------------------------------------------------------------------
# Agent subnet delegation
# ---------------------------------------------------------------------------
$agentSubnet = az network vnet subnet show --ids $agentSubnetId --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect agent subnet '$agentSubnetId'."
}
$delegationServices = @($agentSubnet.delegations | ForEach-Object { $_.serviceName })
if ($delegationServices -notcontains "Microsoft.App/environments") {
    throw "Agent subnet must be delegated to 'Microsoft.App/environments', found: $($delegationServices -join ', ')."
}

# ---------------------------------------------------------------------------
# Cosmos DB posture
# ---------------------------------------------------------------------------
$cosmos = az cosmosdb show --name $cosmosAccount --resource-group $resourceGroup --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Cosmos DB account '$cosmosAccount'."
}
if ($cosmos.publicNetworkAccess -ne "Disabled" -or -not $cosmos.disableLocalAuth) {
    throw "Cosmos DB '$cosmosAccount' must have public network access disabled and local authentication disabled."
}

# ---------------------------------------------------------------------------
# Azure AI Search posture (see infra/UPSTREAM.md: local auth stays enabled
# alongside authOptions.aadOrApiKey for capability-host compatibility).
# ---------------------------------------------------------------------------
$search = az search service show --name $searchService --resource-group $resourceGroup --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Azure AI Search service '$searchService'."
}
if ($search.publicNetworkAccess.ToLowerInvariant() -ne "disabled") {
    throw "Azure AI Search '$searchService' must have public network access disabled, found '$($search.publicNetworkAccess)'."
}
$searchAadOptionPresent = $null -ne $search.authOptions -and $null -ne $search.authOptions.aadOrApiKey
if ($search.disableLocalAuth -and -not $searchAadOptionPresent) {
    throw "Azure AI Search '$searchService' has local auth disabled with no aadOrApiKey fallback; Foundry's AAD connection would be broken."
}
if (-not $search.disableLocalAuth -and -not $searchAadOptionPresent) {
    throw "Azure AI Search '$searchService' must expose authOptions.aadOrApiKey so Foundry's AAD data-plane connection works."
}

# ---------------------------------------------------------------------------
# Storage posture
# ---------------------------------------------------------------------------
$storage = az storage account show --name $storageAccount --resource-group $resourceGroup --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Storage account '$storageAccount'."
}
if ($storage.publicNetworkAccess -ne "Disabled" -or $storage.allowSharedKeyAccess) {
    throw "Storage account '$storageAccount' must have public network access disabled and shared key access disabled."
}

# ---------------------------------------------------------------------------
# Container Registry posture (optional)
# ---------------------------------------------------------------------------
if ($acrName) {
    $acr = az acr show --name $acrName --resource-group $resourceGroup --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Container Registry '$acrName'."
    }
    if ([string]::IsNullOrWhiteSpace($acrDeveloperIpCidr)) {
        if ($acr.publicNetworkAccess -ne "Disabled") {
            throw "Container Registry '$acrName' must have public network access disabled (no developer IP CIDR configured), found '$($acr.publicNetworkAccess)'."
        }
    } else {
        if ($acr.publicNetworkAccess -ne "Enabled" -or $acr.networkRuleSet.defaultAction -ne "Deny") {
            throw "Container Registry '$acrName' with a developer IP CIDR must be Enabled with networkRuleSet.defaultAction Deny."
        }
    }
}

# ---------------------------------------------------------------------------
# Private endpoint approval count/status
# ---------------------------------------------------------------------------
$expectedPeTargets = @($foundryAccount, $searchService, $storageAccount, $cosmosAccount)
if ($acrName) {
    $expectedPeTargets += $acrName
}
$privateEndpoints = az network private-endpoint list --resource-group $resourceGroup --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to list private endpoints in '$resourceGroup'."
}
$expectedPeNames = @($expectedPeTargets | ForEach-Object { "$_-private-endpoint" })
$expectedPeNames += ($privateEndpoints | Where-Object { $_.name -like "ampls-tracing-*-pe" } | Select-Object -ExpandProperty name)
foreach ($expectedPeName in $expectedPeNames) {
    if (@($privateEndpoints | Where-Object { $_.name -eq $expectedPeName }).Count -ne 1) {
        throw "Expected exactly one private endpoint named '$expectedPeName' in '$resourceGroup'."
    }
}
if (@($privateEndpoints | Where-Object { $_.name -like "ampls-tracing-*-pe" }).Count -ne 1) {
    throw "Expected exactly one Azure Monitor Private Link Scope endpoint in '$resourceGroup'."
}
foreach ($pe in $privateEndpoints) {
    $connection = $pe.privateLinkServiceConnections | Select-Object -First 1
    $status = $connection.privateLinkServiceConnectionState.status
    if ($status -ne "Approved") {
        throw "Private endpoint '$($pe.name)' connection status must be 'Approved', found '$status'."
    }
}

# ---------------------------------------------------------------------------
# Private DNS zone links
# ---------------------------------------------------------------------------
$expectedDnsZones = @(
    "privatelink.services.ai.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.search.windows.net",
    "privatelink.blob.core.windows.net",
    "privatelink.documents.azure.com",
    "privatelink.monitor.azure.com",
    "privatelink.oms.opinsights.azure.com",
    "privatelink.ods.opinsights.azure.com",
    "privatelink.agentsvc.azure-automation.net"
)
if ($acrName) {
    $expectedDnsZones += "privatelink.azurecr.io"
}
foreach ($zoneName in $expectedDnsZones) {
    $links = az network private-dns link vnet list `
        --resource-group $resourceGroup `
        --zone-name $zoneName `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list VNet links for private DNS zone '$zoneName'."
    }
    $matchingLink = $links | Where-Object {
        $_.provisioningState -eq "Succeeded" -and $_.virtualNetwork.id -like "*/virtualNetworks/$vnetName"
    } | Select-Object -First 1
    if (-not $matchingLink) {
        throw "Private DNS zone '$zoneName' has no Succeeded VNet link to '$vnetName'."
    }
}

# ---------------------------------------------------------------------------
# Hosted agent: show + grounded invoke (also proves Cosmos/Search startup checks)
# ---------------------------------------------------------------------------
$agent = azd ai agent show $AgentName --environment $EnvironmentName --output json | ConvertFrom-Json
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

Write-Output "BYO deployment validation passed."
Write-Output "Foundry account: Enabled + Deny default + one narrow CIDR allowlist ($allowedClientIpCidr); network injection scenario 'agent' with useMicrosoftManagedNetwork=false on the expected agent subnet; agent subnet delegated to Microsoft.App/environments."
Write-Output "Cosmos DB / Azure AI Search / Storage$(if ($acrName) { ' / Container Registry' }) public access posture verified; all private endpoints Approved; all private DNS zone VNet links Succeeded."
Write-Output "Successful agent startup and grounded invoke also verify Search indexing and the Cosmos write/read/delete connectivity check."
