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
        [Parameter(Mandatory)][string]$ConnectionGroupId,
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
            $_.properties.groupIds -contains $ConnectionGroupId -and
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

function Wait-SharedPrivateLinkTerminalState {
    param(
        [Parameter(Mandatory)][string]$SharedLinkUri,
        [Parameter(Mandatory)][string]$SharedPrivateLinkName
    )

    for ($attempt = 1; $attempt -le 90; $attempt++) {
        $sharedLink = az rest --method get --uri $SharedLinkUri `
            --output json | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to refresh shared private link '$SharedPrivateLinkName'."
        }
        if ($sharedLink.properties.provisioningState -in @("Succeeded", "Failed")) {
            return $sharedLink
        }
        Start-Sleep -Seconds 10
    }

    throw (
        "Shared private link '$SharedPrivateLinkName' did not reach a " +
        "terminal provisioning state."
    )
}

function Ensure-SharedPrivateLink {
    param(
        [Parameter(Mandatory)][string]$SearchServiceResourceId,
        [Parameter(Mandatory)][string]$SharedPrivateLinkName,
        [Parameter(Mandatory)][string]$TargetResourceId,
        [Parameter(Mandatory)][string]$GroupId,
        [Parameter(Mandatory)][string]$RequestMessage
    )

    $sharedLinkUri = (
        "https://management.azure.com$SearchServiceResourceId/" +
        "sharedPrivateLinkResources/$SharedPrivateLinkName" +
        "?api-version=2025-05-01"
    )
    $sharedLink = az rest --method get --uri $sharedLinkUri `
        --output json 2>$null | ConvertFrom-Json
    if ($LASTEXITCODE -eq 0) {
        $sharedLink = Wait-SharedPrivateLinkTerminalState `
            -SharedLinkUri $sharedLinkUri `
            -SharedPrivateLinkName $SharedPrivateLinkName
        if ($sharedLink.properties.provisioningState -eq "Succeeded") {
            if (
                $sharedLink.properties.privateLinkResourceId -ne $TargetResourceId -or
                $sharedLink.properties.groupId -ne $GroupId
            ) {
                throw (
                    "Shared private link '$SharedPrivateLinkName' targets an " +
                    "unexpected resource or group."
                )
            }
            return
        }

        az rest --method delete --uri $sharedLinkUri --output none
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to delete failed shared private link '$SharedPrivateLinkName'."
        }
        for ($attempt = 1; $attempt -le 60; $attempt++) {
            az rest --method get --uri $sharedLinkUri --output none 2>$null
            if ($LASTEXITCODE -ne 0) {
                break
            }
            Start-Sleep -Seconds 10
        }
        if ($attempt -gt 60) {
            throw "Timed out deleting shared private link '$SharedPrivateLinkName'."
        }
    }

    $requestPath = [System.IO.Path]::GetTempFileName()
    try {
        @{
            properties = @{
                groupId = $GroupId
                privateLinkResourceId = $TargetResourceId
                requestMessage = $RequestMessage
            }
        } | ConvertTo-Json -Depth 5 | Set-Content `
            -Path $requestPath `
            -Encoding utf8NoBOM

        az rest --method put --uri $sharedLinkUri `
            --headers "Content-Type=application/json" `
            --body "@$requestPath" `
            --output none
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to create shared private link '$SharedPrivateLinkName'."
        }
    } finally {
        Remove-Item -LiteralPath $requestPath -Force -ErrorAction SilentlyContinue
    }

    $sharedLink = Wait-SharedPrivateLinkTerminalState `
        -SharedLinkUri $sharedLinkUri `
        -SharedPrivateLinkName $SharedPrivateLinkName
    if ($sharedLink.properties.provisioningState -ne "Succeeded") {
        throw (
            "Shared private link '$SharedPrivateLinkName' provisioning failed."
        )
    }
}

function Get-SharedPrivateLinkName {
    param(
        [Parameter(Mandatory)][string]$SearchServiceResourceId,
        [Parameter(Mandatory)][string]$TargetResourceId,
        [Parameter(Mandatory)][string]$GroupId
    )

    $uri = (
        "https://management.azure.com$SearchServiceResourceId/" +
        "sharedPrivateLinkResources?api-version=2025-05-01"
    )
    $resources = az rest --method get --uri $uri `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list Azure AI Search shared private links."
    }

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
    return $matches[0].name
}

$searchServiceResourceId = Get-AzdValue "AZURE_SEARCH_SERVICE_RESOURCE_ID"
$storageId = Get-AzdValue "FOUNDRY_IQ_STORAGE_ACCOUNT_ID"
$foundryAccountId = Get-AzdValue "AZURE_AI_ACCOUNT_RESOURCE_ID"
$resourceGroup = Get-AzdValue "AZURE_RESOURCE_GROUP"
$intakeCosmosAccountName = Get-AzdValue "INTAKE_COSMOS_ACCOUNT_NAME"
$intakeCosmosAccountId = az cosmosdb show `
    --name $intakeCosmosAccountName `
    --resource-group $resourceGroup `
    --query id `
    --output tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($intakeCosmosAccountId)) {
    throw "Unable to resolve intake Cosmos DB resource ID."
}
$intakeCosmosAccountId = $intakeCosmosAccountId.Trim()
$blobLinkName = Get-SharedPrivateLinkName `
    -SearchServiceResourceId $searchServiceResourceId `
    -TargetResourceId $storageId `
    -GroupId "blob"

Approve-SearchConnection `
    -TargetResourceId $storageId `
    -SearchServiceResourceId $searchServiceResourceId `
    -SharedPrivateLinkName $blobLinkName `
    -ConnectionGroupId "blob" `
    -RequestMessage "Approve private blob access for Foundry IQ ingestion."

$blobLinkPrefix = "foundry-iq-blob-"
if (-not $blobLinkName.StartsWith($blobLinkPrefix)) {
    throw "Unexpected Foundry IQ blob shared-link name '$blobLinkName'."
}
$linkSuffix = $blobLinkName.Substring($blobLinkPrefix.Length)
$foundryLinkName = "foundry-iq-models-$linkSuffix"
Ensure-SharedPrivateLink `
    -SearchServiceResourceId $searchServiceResourceId `
    -SharedPrivateLinkName $foundryLinkName `
    -TargetResourceId $foundryAccountId `
    -GroupId "openai_account" `
    -RequestMessage "Approve private model access for Foundry IQ ingestion and retrieval."
Approve-SearchConnection `
    -TargetResourceId $foundryAccountId `
    -SearchServiceResourceId $searchServiceResourceId `
    -SharedPrivateLinkName $foundryLinkName `
    -ConnectionGroupId "account" `
    -RequestMessage "Approve private model access for Foundry IQ ingestion and retrieval."

$intakeCosmosLinkName = Get-SharedPrivateLinkName `
    -SearchServiceResourceId $searchServiceResourceId `
    -TargetResourceId $intakeCosmosAccountId `
    -GroupId "Sql"
Approve-SearchConnection `
    -TargetResourceId $intakeCosmosAccountId `
    -SearchServiceResourceId $searchServiceResourceId `
    -SharedPrivateLinkName $intakeCosmosLinkName `
    -ConnectionGroupId "Sql" `
    -RequestMessage "Approve private Cosmos DB access for intake search indexing."

Write-Output "Approved Search outbound private endpoint connections."
