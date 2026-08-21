param(
    [string]$EnvironmentName = "maf-poc-private",
    [string]$AgentName = "maf-poc-agent",
    [string]$ResourceGroup,
    [string]$CosmosAccountName,
    [string]$SearchServiceName
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

function Add-AzureRole {
    param(
        [Parameter(Mandatory)][string]$PrincipalId,
        [Parameter(Mandatory)][string]$RoleName,
        [Parameter(Mandatory)][string]$Scope
    )

    $existing = az role assignment list `
        --assignee-object-id $PrincipalId `
        --scope $Scope `
        --query "[?roleDefinitionName=='$RoleName'].id | [0]" `
        --output tsv
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect role '$RoleName' at '$Scope'."
    }
    if ($existing) {
        return
    }

    az role assignment create `
        --assignee-object-id $PrincipalId `
        --assignee-principal-type ServicePrincipal `
        --role $RoleName `
        --scope $Scope `
        --output none
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to assign role '$RoleName' at '$Scope'."
    }
}

if (-not $ResourceGroup) {
    $ResourceGroup = Get-AzdValue "AZURE_RESOURCE_GROUP"
}
if (-not $CosmosAccountName) {
    $CosmosAccountName = Get-AzdValue "AZURE_COSMOS_ACCOUNT_NAME"
}
if (-not $SearchServiceName) {
    $SearchServiceName = Get-AzdValue "AZURE_SEARCH_SERVICE_NAME"
}

$agent = azd ai agent show $AgentName --environment $EnvironmentName --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve hosted agent '$AgentName'."
}

$principalId = $agent.instance_identity.principal_id
if (-not $principalId) {
    $principalId = $agent.instanceIdentity.principalId
}
if (-not $principalId) {
    throw "The hosted agent does not expose an instance identity."
}

$cosmosRoleId = "00000000-0000-0000-0000-000000000002"
$cosmosAssignments = az cosmosdb sql role assignment list `
    --account-name $CosmosAccountName `
    --resource-group $ResourceGroup | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Cosmos DB role assignments."
}

$hasCosmosRole = $cosmosAssignments | Where-Object {
    $_.principalId -eq $principalId -and
    $_.roleDefinitionId.EndsWith($cosmosRoleId)
}
if (-not $hasCosmosRole) {
    az cosmosdb sql role assignment create `
        --account-name $CosmosAccountName `
        --resource-group $ResourceGroup `
        --scope "/" `
        --principal-id $principalId `
        --role-definition-id $cosmosRoleId `
        --output none
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to assign the Cosmos DB Built-in Data Contributor role."
    }
}

$searchId = az search service show `
    --name $SearchServiceName `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $searchId) {
    throw "Unable to resolve Azure AI Search service '$SearchServiceName'."
}

Add-AzureRole $principalId "Search Service Contributor" $searchId
Add-AzureRole $principalId "Search Index Data Contributor" $searchId
Add-AzureRole $principalId "Search Index Data Reader" $searchId

Write-Output "Assigned Cosmos DB and Azure AI Search roles to $principalId."
