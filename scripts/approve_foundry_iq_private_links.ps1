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

function Approve-SearchConnection {
    param(
        [Parameter(Mandatory)][string]$TargetResourceId,
        [Parameter(Mandatory)][string]$SearchServiceResourceId,
        [Parameter(Mandatory)][string]$SharedPrivateLinkName,
        [Parameter(Mandatory)][string]$GroupId,
        [Parameter(Mandatory)][string]$RequestMessage
    )

    $sharedLinkUri = (
        "https://management.azure.com$SearchServiceResourceId/" +
        "sharedPrivateLinkResources/$SharedPrivateLinkName" +
        "?api-version=2025-05-01"
    )
    $sharedLink = az rest --method get --uri $sharedLinkUri `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read shared private link '$SharedPrivateLinkName'."
    }
    if ($sharedLink.properties.status -eq "Approved") {
        return
    }
    if ($sharedLink.properties.status -ne "Pending") {
        throw (
            "Shared private link '$SharedPrivateLinkName' is " +
            "'$($sharedLink.properties.status)'."
        )
    }

    $connections = az network private-endpoint-connection list `
        --id $TargetResourceId `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list private endpoint connections on '$TargetResourceId'."
    }

    $searchConnections = @(
        $connections | Where-Object {
            $_.properties.privateLinkServiceConnectionState.status -eq "Pending" -and
            $_.properties.groupIds -contains $GroupId -and
            $_.properties.privateLinkServiceConnectionState.description -eq $RequestMessage
        }
    )
    if ($searchConnections.Count -ne 1) {
        throw (
            "Expected exactly one Azure AI Search private endpoint connection " +
            "on '$TargetResourceId', found $($searchConnections.Count)."
        )
    }

    $connection = $searchConnections[0]
    $status = $connection.properties.privateLinkServiceConnectionState.status
    if ($status -eq "Approved") {
        return
    }
    if ($status -ne "Pending") {
        throw "Private endpoint connection '$($connection.id)' is '$status'."
    }

    az network private-endpoint-connection approve `
        --id $connection.id `
        --description "Approved for Foundry IQ." `
        --output none
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to approve private endpoint connection '$($connection.id)'."
    }

    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $sharedLink = az rest --method get --uri $sharedLinkUri `
            --output json | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to refresh shared private link '$SharedPrivateLinkName'."
        }
        if ($sharedLink.properties.status -eq "Approved") {
            return
        }
        Start-Sleep -Seconds 10
    }

    throw (
        "Shared private link '$SharedPrivateLinkName' did not become approved " +
        "after its target connection was approved."
    )
}

$searchServiceResourceId = Get-AzdValue "AZURE_SEARCH_SERVICE_RESOURCE_ID"
$storageId = Get-AzdValue "FOUNDRY_IQ_STORAGE_ACCOUNT_ID"
$foundryAccountId = Get-AzdValue "AZURE_AI_ACCOUNT_RESOURCE_ID"
$blobLinkName = Get-AzdValue "FOUNDRY_IQ_BLOB_SHARED_PRIVATE_LINK_NAME"
$foundryLinkName = Get-AzdValue "FOUNDRY_IQ_FOUNDRY_SHARED_PRIVATE_LINK_NAME"

Approve-SearchConnection `
    -TargetResourceId $storageId `
    -SearchServiceResourceId $searchServiceResourceId `
    -SharedPrivateLinkName $blobLinkName `
    -GroupId "blob" `
    -RequestMessage "Approve private blob access for Foundry IQ ingestion."
Approve-SearchConnection `
    -TargetResourceId $foundryAccountId `
    -SearchServiceResourceId $searchServiceResourceId `
    -SharedPrivateLinkName $foundryLinkName `
    -GroupId "openai_account" `
    -RequestMessage "Approve private model access for Foundry IQ ingestion and retrieval."

Write-Output "Approved Foundry IQ outbound private endpoint connections."
