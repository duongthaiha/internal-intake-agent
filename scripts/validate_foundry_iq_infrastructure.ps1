param(
    [string]$EnvironmentName = "maf-poc-byo"
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

function Get-ArmResource {
    param(
        [Parameter(Mandatory)][string]$ResourceId,
        [Parameter(Mandatory)][string]$ApiVersion
    )

    $resource = az rest --method get --uri (
        "https://management.azure.com$ResourceId" +
        "?api-version=$ApiVersion"
    ) --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Azure resource '$ResourceId'."
    }
    return $resource
}

function Assert-RoleAssignment {
    param(
        [Parameter(Mandatory)][string]$Scope,
        [Parameter(Mandatory)][string]$PrincipalId,
        [Parameter(Mandatory)][string]$RoleDefinitionId
    )

    $count = az role assignment list `
        --scope $Scope `
        --assignee-object-id $PrincipalId `
        --role $RoleDefinitionId `
        --query "length(@)" `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or [int]$count -ne 1) {
        throw (
            "Expected one role '$RoleDefinitionId' assignment for principal " +
            "'$PrincipalId' at '$Scope', found '$count'."
        )
    }
}

function Get-SharedPrivateLink {
    param(
        [Parameter(Mandatory)][string]$SearchServiceResourceId,
        [Parameter(Mandatory)][string]$TargetResourceId,
        [Parameter(Mandatory)][string]$GroupId
    )

    $resources = Get-ArmResource (
        "$SearchServiceResourceId/sharedPrivateLinkResources"
    ) "2025-05-01"
    $matches = @(
        $resources.value | Where-Object {
            $_.properties.privateLinkResourceId -eq $TargetResourceId -and
            $_.properties.groupId -eq $GroupId -and
            $_.properties.provisioningState -ne "Incomplete"
        }
    )
    if ($matches.Count -ne 1) {
        throw (
            "Expected one '$GroupId' shared private link from Search to " +
            "'$TargetResourceId', found $($matches.Count)."
        )
    }
    return $matches[0]
}

$storageId = Get-AzdValue "FOUNDRY_IQ_STORAGE_ACCOUNT_ID"
$storageName = ($storageId -split "/")[-1]
$containerName = Get-AzdValue "FOUNDRY_IQ_STORAGE_CONTAINER_NAME"
$searchId = Get-AzdValue "AZURE_SEARCH_SERVICE_RESOURCE_ID"
$foundryAccountId = Get-AzdValue "AZURE_AI_ACCOUNT_RESOURCE_ID"
$chatDeploymentName = Get-AzdValue "FOUNDRY_IQ_CHAT_DEPLOYMENT_NAME"
$embeddingDeploymentName = Get-AzdValue "FOUNDRY_IQ_EMBEDDING_DEPLOYMENT_NAME"

$resourceGroupId = $storageId -replace "/providers/.*$", ""
$privateEndpointId = (
    "$resourceGroupId/providers/Microsoft.Network/privateEndpoints/" +
    "$storageName-private-endpoint"
)
$privateDnsGroupId = (
    "$privateEndpointId/privateDnsZoneGroups/$storageName-dns-group"
)

$storage = Get-ArmResource $storageId "2025-01-01"
if (
    $storage.properties.publicNetworkAccess -ne "Disabled" -or
    $storage.properties.allowSharedKeyAccess -ne $false -or
    $storage.properties.allowBlobPublicAccess -ne $false -or
    $storage.properties.minimumTlsVersion -ne "TLS1_2"
) {
    throw "Foundry IQ Storage security posture validation failed."
}

$containerId = "$storageId/blobServices/default/containers/$containerName"
$container = Get-ArmResource $containerId "2025-01-01"
if ($container.properties.publicAccess -ne "None") {
    throw "Foundry IQ knowledge container permits public access."
}

$null = Get-ArmResource $privateEndpointId "2024-05-01"
$null = Get-ArmResource $privateDnsGroupId "2024-05-01"

$links = @(
    Get-SharedPrivateLink $searchId $storageId "blob"
    Get-SharedPrivateLink $searchId $foundryAccountId "openai_account"
)
foreach ($link in $links) {
    if ($link.properties.status -ne "Approved") {
        throw "Shared private link '$($link.name)' is not approved."
    }
}

$storageBlobDataReader = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
$storageBlobDataContributor = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
$searchServiceContributor = "7ca78c08-252a-4471-8644-bb5ff32d4ba0"
$searchIndexDataContributor = "8ebe5a00-799e-43f5-93ac-243d3dce84a7"
$cognitiveServicesUser = "a97b65f3-24c7-4388-baec-2e87135dc908"

$search = Get-ArmResource $searchId "2025-05-01"
$searchPrincipalId = $search.identity.principalId
if ([string]::IsNullOrWhiteSpace($searchPrincipalId)) {
    throw "Azure AI Search does not have a system-assigned managed identity."
}

$contributorAssignments = @(
    az role assignment list `
        --scope $storageId `
        --role $storageBlobDataContributor `
        --output json | ConvertFrom-Json | Where-Object {
            $_.scope -eq $storageId
        }
)
if ($LASTEXITCODE -ne 0 -or $contributorAssignments.Count -ne 1) {
    throw (
        "Expected one direct Storage Blob Data Contributor assignment on " +
        "the Foundry IQ source account."
    )
}
$provisionerPrincipalId = $contributorAssignments[0].principalId

Assert-RoleAssignment $storageId $searchPrincipalId $storageBlobDataReader
Assert-RoleAssignment $storageId $provisionerPrincipalId $storageBlobDataContributor
Assert-RoleAssignment $searchId $provisionerPrincipalId $searchServiceContributor
Assert-RoleAssignment $searchId $provisionerPrincipalId $searchIndexDataContributor
Assert-RoleAssignment $foundryAccountId $searchPrincipalId $cognitiveServicesUser

$null = Get-ArmResource (
    "$foundryAccountId/deployments/$chatDeploymentName"
) "2024-10-01"
$null = Get-ArmResource (
    "$foundryAccountId/deployments/$embeddingDeploymentName"
) "2024-10-01"

Write-Output "Foundry IQ infrastructure validation succeeded."
