<#
.SYNOPSIS
    Validates the BYO-VNet (dual inbound path) Foundry hosted-agent deployment.

.DESCRIPTION
    Replaces validate_private_deployment.ps1's managed-network checks with the
    checks that actually apply to the current infra/main.bicep BYO-VNet setup:
      - Foundry account public network access is Enabled (dual inbound path)
        with networkAcls.defaultAction Deny and exactly the expected
        allowlisted client IPv4 CIDR.
      - Foundry networkInjections has the "agent" scenario pointed at the
        expected agent subnet with useMicrosoftManagedNetwork=false.
      - The agent subnet has the Microsoft.App/environments delegation.
      - Foundry, Cosmos DB, Azure AI Search, Storage, and Container Registry
        carry the expected security tag and selected-network ACL.
      - Every private endpoint connection is Approved (count + status).
      - Every expected private DNS zone has a Succeeded VNet link.
      - The hosted agent is reachable and its response is grounded (includes
        the model deployment name and the expected knowledge source), which
        also proves the Cosmos DB and Search startup checks passed.
      - The intake API uses a separate Cosmos DB account, has the expected
        container-scoped data role, and passes both liveness and readiness.
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

function Assert-SecurityControlTag {
    param(
        [Parameter(Mandatory)]$Resource,
        [Parameter(Mandatory)][string]$DisplayName
    )

    if ($Resource.tags.SecurityControl -ne "Ignore") {
        throw "$DisplayName must have tag 'SecurityControl=Ignore'."
    }
}

function Assert-SingleIpRule {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [object[]]$Rules,
        [Parameter(Mandatory)][string]$ExpectedValue,
        [Parameter(Mandatory)][string]$DisplayName
    )

    if ($Rules.Count -ne 1 -or [string]$Rules[0] -ne $ExpectedValue) {
        throw (
            "$DisplayName must contain exactly one IP rule equal to '$ExpectedValue'; " +
            "found: $($Rules -join ', ')."
        )
    }
}

$resourceGroup = Get-AzdValue "AZURE_RESOURCE_GROUP"
$foundryAccount = Get-AzdValue "AZURE_AI_ACCOUNT_NAME"
$foundryAccountIsExisting = (Get-AzdValueOrDefault "AZURE_AI_ACCOUNT_IS_EXISTING" "false") -eq "true"
$foundryProjectId = Get-AzdValue "AZURE_AI_PROJECT_ID"
$cosmosAccount = Get-AzdValue "AZURE_COSMOS_ACCOUNT_NAME"
$cosmosAccountIsExisting = (Get-AzdValueOrDefault "AZURE_COSMOS_ACCOUNT_IS_EXISTING" "false") -eq "true"
$searchService = Get-AzdValue "AZURE_SEARCH_SERVICE_NAME"
$searchServiceIsExisting = (Get-AzdValueOrDefault "AZURE_SEARCH_SERVICE_IS_EXISTING" "false") -eq "true"
$storageAccount = Get-AzdValue "AZURE_STORAGE_ACCOUNT_NAME"
$storageAccountIsExisting = (Get-AzdValueOrDefault "AZURE_STORAGE_ACCOUNT_IS_EXISTING" "false") -eq "true"
$modelDeployment = Get-AzdValue "AZURE_AI_MODEL_DEPLOYMENT_NAME"
$vnetName = Get-AzdValue "AZURE_VIRTUAL_NETWORK_NAME"
$agentSubnetId = Get-AzdValue "AZURE_AGENT_SUBNET_ID"
$allowedClientIpCidr = Get-AzdValue "AZURE_ALLOWED_CLIENT_IP_CIDR"
$allowedClientIp = $allowedClientIpCidr -replace '/32$', ''
$acrName = Get-AzdValueOrDefault "AZURE_CONTAINER_REGISTRY_NAME"
$acrDeveloperIpCidr = Get-AzdValueOrDefault "AZURE_ACR_DEVELOPER_IP_CIDR"
$intakeCosmosAccount = Get-AzdValue "INTAKE_COSMOS_ACCOUNT_NAME"
$intakeCosmosDatabase = Get-AzdValue "INTAKE_COSMOS_DATABASE_NAME"
$intakeCosmosContainer = Get-AzdValue "INTAKE_COSMOS_CONTAINER_NAME"
$intakeApiName = Get-AzdValue "SERVICE_INTAKE_API_NAME"
$intakeApiUri = Get-AzdValue "SERVICE_INTAKE_API_URI"
$intakeSubnetId = Get-AzdValue "AZURE_INTAKE_CONTAINER_APPS_SUBNET_ID"

if ($intakeCosmosAccount -eq $cosmosAccount) {
    throw "Intake records and conversation history must use separate Cosmos DB accounts."
}

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
if (-not $foundryAccountIsExisting) {
    Assert-SecurityControlTag -Resource $foundry -DisplayName "Foundry account '$foundryAccount'"
    if ($foundry.properties.disableLocalAuth) {
        throw "Foundry account '$foundryAccount' must retain key-based local authentication."
    }
    if ($foundry.properties.publicNetworkAccess -ne "Enabled") {
        throw "Foundry account '$foundryAccount' publicNetworkAccess must be 'Enabled' (dual inbound path), found '$($foundry.properties.publicNetworkAccess)'."
    }
    if ($foundry.properties.networkAcls.defaultAction -ne "Deny") {
        throw "Foundry account '$foundryAccount' networkAcls.defaultAction must be 'Deny', found '$($foundry.properties.networkAcls.defaultAction)'."
    }
    $ipRules = @($foundry.properties.networkAcls.ipRules | ForEach-Object { $_.value })
    Assert-SingleIpRule -Rules $ipRules -ExpectedValue $allowedClientIp -DisplayName "Foundry account '$foundryAccount'"
}

$foundryProject = az resource show --ids $foundryProjectId --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Foundry project '$foundryProjectId'."
}
Assert-SecurityControlTag -Resource $foundryProject -DisplayName "Foundry project '$foundryProjectId'"

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
if (-not $cosmos.disableLocalAuth) {
    throw "Cosmos DB '$cosmosAccount' must have local authentication disabled."
}
if (-not $cosmosAccountIsExisting) {
    Assert-SecurityControlTag -Resource $cosmos -DisplayName "Cosmos DB '$cosmosAccount'"
    if ($cosmos.publicNetworkAccess -ne "Enabled") {
        throw "Cosmos DB '$cosmosAccount' must use selected-network public access."
    }
    $cosmosIpRules = @($cosmos.ipRules | ForEach-Object { $_.ipAddressOrRange })
    Assert-SingleIpRule -Rules $cosmosIpRules -ExpectedValue $allowedClientIpCidr -DisplayName "Cosmos DB '$cosmosAccount'"
}

$intakeCosmos = az cosmosdb show --name $intakeCosmosAccount --resource-group $resourceGroup --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect intake Cosmos DB account '$intakeCosmosAccount'."
}
Assert-SecurityControlTag -Resource $intakeCosmos -DisplayName "Intake Cosmos DB '$intakeCosmosAccount'"
if ($intakeCosmos.publicNetworkAccess -ne "Enabled" -or -not $intakeCosmos.disableLocalAuth) {
    throw "Intake Cosmos DB '$intakeCosmosAccount' must use selected-network public access with local authentication disabled."
}
$intakeCosmosIpRules = @($intakeCosmos.ipRules | ForEach-Object { $_.ipAddressOrRange })
Assert-SingleIpRule -Rules $intakeCosmosIpRules -ExpectedValue $allowedClientIpCidr -DisplayName "Intake Cosmos DB '$intakeCosmosAccount'"
$intakeContainer = az cosmosdb sql container show `
    --account-name $intakeCosmosAccount `
    --database-name $intakeCosmosDatabase `
    --name $intakeCosmosContainer `
    --resource-group $resourceGroup `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect intake Cosmos container '$intakeCosmosContainer'."
}
$partitionKey = if ($intakeContainer.resource.partitionKey) {
    $intakeContainer.resource.partitionKey
} else {
    $intakeContainer.partitionKey
}
if (
    $partitionKey.kind -ne "MultiHash" -or
    @($partitionKey.paths).Count -ne 2 -or
    $partitionKey.paths[0] -ne "/tenantId" -or
    $partitionKey.paths[1] -ne "/id"
) {
    throw "Intake Cosmos container must use the hierarchical partition key '/tenantId', '/id'."
}

# ---------------------------------------------------------------------------
# Intake Container App posture, data-plane role, and health
# ---------------------------------------------------------------------------
$intakeSubnet = az network vnet subnet show --ids $intakeSubnetId --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect intake Container Apps subnet '$intakeSubnetId'."
}
$intakeDelegations = @($intakeSubnet.delegations | ForEach-Object { $_.serviceName })
if ($intakeDelegations -notcontains "Microsoft.App/environments") {
    throw "Intake subnet must be delegated to 'Microsoft.App/environments'."
}

$intakeApp = az containerapp show --name $intakeApiName --resource-group $resourceGroup --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect intake Container App '$intakeApiName'."
}
if (
    -not $intakeApp.properties.configuration.ingress.external -or
    $intakeApp.properties.configuration.ingress.targetPort -ne 8000 -or
    -not $intakeApp.identity.principalId
) {
    throw "Intake Container App must use external HTTPS ingress on port 8000 and a managed identity."
}
$intakeImage = $intakeApp.properties.template.containers[0].image
if ($intakeImage -match "containerapps-helloworld") {
    throw "Intake Container App is still running the provisioning placeholder image."
}

$expectedIntakeContainerScope = "$($intakeCosmos.id)/dbs/$intakeCosmosDatabase/colls/$intakeCosmosContainer"
$intakeRoleAssignments = @(
    az cosmosdb sql role assignment list `
        --account-name $intakeCosmosAccount `
        --resource-group $resourceGroup `
        --output json | ConvertFrom-Json
)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect intake Cosmos DB SQL role assignments."
}
$intakeDataRole = $intakeRoleAssignments |
    Where-Object {
        $_.principalId -eq $intakeApp.identity.principalId -and
        $_.scope -eq $expectedIntakeContainerScope -and
        $_.roleDefinitionId -match "/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002$"
    } |
    Select-Object -First 1
if (-not $intakeDataRole) {
    throw (
        "Intake Container App identity '$($intakeApp.identity.principalId)' must have " +
        "Cosmos DB Built-in Data Contributor scoped to '$expectedIntakeContainerScope'."
    )
}

$liveness = Invoke-RestMethod -Uri "$($intakeApiUri.TrimEnd('/'))/health/live" -Method Get -TimeoutSec 30
if ($liveness.status -ne "ok") {
    throw "Intake API liveness check failed."
}
$readiness = Invoke-RestMethod -Uri "$($intakeApiUri.TrimEnd('/'))/health/ready" -Method Get -TimeoutSec 30
if ($readiness.status -ne "ready") {
    throw "Intake API readiness check failed."
}

# ---------------------------------------------------------------------------
# Azure AI Search posture (see infra/UPSTREAM.md: local auth stays enabled
# alongside authOptions.aadOrApiKey for capability-host compatibility).
# ---------------------------------------------------------------------------
$search = az search service show --name $searchService --resource-group $resourceGroup --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Azure AI Search service '$searchService'."
}
if (-not $searchServiceIsExisting) {
    Assert-SecurityControlTag -Resource $search -DisplayName "Azure AI Search '$searchService'"
    if ($search.publicNetworkAccess.ToLowerInvariant() -ne "enabled" -or $search.networkRuleSet.bypass -ne "None") {
        throw "Azure AI Search '$searchService' must use selected-network public access with no ACL bypass."
    }
    $searchIpRules = @($search.networkRuleSet.ipRules | ForEach-Object { $_.value })
    Assert-SingleIpRule -Rules $searchIpRules -ExpectedValue $allowedClientIpCidr -DisplayName "Azure AI Search '$searchService'"
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
if ($storage.allowSharedKeyAccess) {
    throw "Storage account '$storageAccount' must have shared key access disabled."
}
if (-not $storageAccountIsExisting) {
    Assert-SecurityControlTag -Resource $storage -DisplayName "Storage account '$storageAccount'"
    if ($storage.publicNetworkAccess -ne "Enabled" -or $storage.networkRuleSet.defaultAction -ne "Deny") {
        throw "Storage account '$storageAccount' must use selected-network public access."
    }
    $storageIpRules = @($storage.networkRuleSet.ipRules | ForEach-Object { $_.ipAddressOrRange })
    Assert-SingleIpRule -Rules $storageIpRules -ExpectedValue $allowedClientIpCidr -DisplayName "Storage account '$storageAccount'"
}

# ---------------------------------------------------------------------------
# Container Registry posture (optional)
# ---------------------------------------------------------------------------
if ($acrName) {
    $acr = az acr show --name $acrName --resource-group $resourceGroup --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Container Registry '$acrName'."
    }
    Assert-SecurityControlTag -Resource $acr -DisplayName "Container Registry '$acrName'"
    if ($acr.publicNetworkAccess -ne "Enabled" -or $acr.networkRuleSet.defaultAction -ne "Deny") {
        throw "Container Registry '$acrName' must use selected-network public access with networkRuleSet.defaultAction Deny."
    }
    $acrIpRules = @($acr.networkRuleSet.ipRules | ForEach-Object { $_.value })
    Assert-SingleIpRule -Rules $acrIpRules -ExpectedValue $acrDeveloperIpCidr -DisplayName "Container Registry '$acrName'"
}

# ---------------------------------------------------------------------------
# Private endpoint approval count/status
# ---------------------------------------------------------------------------
$expectedPeTargets = @($foundryAccount, $searchService, $storageAccount, $cosmosAccount, $intakeCosmosAccount)
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
Write-Output "Foundry account: Enabled + Deny default + one CIDR allowlist ($allowedClientIpCidr); network injection scenario 'agent' with useMicrosoftManagedNetwork=false on the expected agent subnet; agent subnet delegated to Microsoft.App/environments."
Write-Output "SecurityControl=Ignore and selected-network ACLs verified for template-created Foundry, Cosmos DB, Azure AI Search, Storage$(if ($acrName) { ', and Container Registry' }); all private endpoints Approved; all private DNS zone VNet links Succeeded."
Write-Output "Successful agent startup and grounded invoke also verify Search indexing and the Cosmos write/read/delete connectivity check."
Write-Output "Intake API: deployed image is ready; dedicated subnet, managed identity, separate private Cosmos account, container-scoped data role, and partitioning verified."
