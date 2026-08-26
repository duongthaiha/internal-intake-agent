param(
    [string]$EnvironmentName = "maf-poc-byo",
    [int]$RetryCount = 30,
    [int]$RetryDelaySeconds = 10
)

$ErrorActionPreference = "Stop"
$previousUserAgent = $env:AZURE_DEV_USER_AGENT
$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"

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
        [Parameter(Mandatory)][string]$Default
    )

    $value = azd env get-value $Name --environment $EnvironmentName 2>$null
    $text = ($value | Out-String).Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        [string]::IsNullOrWhiteSpace($text) -or
        $text.StartsWith("ERROR:", [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        return $Default
    }
    return $text
}

function Get-SearchResource {
    param([Parameter(Mandatory)][string]$Path)

    $resource = az rest `
        --method get `
        --url "$searchEndpoint/$Path`?api-version=2025-09-01" `
        --resource "https://search.azure.com" `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Azure AI Search resource '$Path'."
    }
    return $resource
}

try {
    $resourceGroup = Get-AzdValue "AZURE_RESOURCE_GROUP"
    $searchEndpoint = Get-AzdValue "AZURE_SEARCH_ENDPOINT"
    $searchServiceId = Get-AzdValue "AZURE_SEARCH_SERVICE_RESOURCE_ID"
    $intakeCosmosAccountName = Get-AzdValue "INTAKE_COSMOS_ACCOUNT_NAME"
    $intakeCosmosDatabaseName = Get-AzdValue "INTAKE_COSMOS_DATABASE_NAME"
    $intakeCosmosContainerName = Get-AzdValue "INTAKE_COSMOS_CONTAINER_NAME"
    $indexName = Get-AzdValueOrDefault "INTAKE_SEARCH_INDEX_NAME" "intake-requests"
    $indexerInterval = Get-AzdValueOrDefault "INTAKE_SEARCH_INDEXER_INTERVAL" "PT15M"
    $dataSourceName = "$indexName-cosmos"
    $skillsetName = "$indexName-skillset"
    $indexerName = "$indexName-indexer"

    $search = az rest `
        --method get `
        --url "https://management.azure.com$searchServiceId`?api-version=2025-05-01" `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Azure AI Search service."
    }
    $searchPrincipalId = $search.identity.principalId
    if ([string]::IsNullOrWhiteSpace($searchPrincipalId)) {
        throw "Azure AI Search must have a system-assigned managed identity."
    }

    $intakeCosmosAccountId = az cosmosdb show `
        --name $intakeCosmosAccountName `
        --resource-group $resourceGroup `
        --query id `
        --output tsv
    if (
        $LASTEXITCODE -ne 0 -or
        [string]::IsNullOrWhiteSpace($intakeCosmosAccountId)
    ) {
        throw "Unable to resolve intake Cosmos DB resource ID."
    }
    $intakeCosmosAccountId = $intakeCosmosAccountId.Trim()
    $expectedCosmosScope = (
        "$intakeCosmosAccountId/dbs/$intakeCosmosDatabaseName/" +
        "colls/$intakeCosmosContainerName"
    )
    $assignments = @(
        az cosmosdb sql role assignment list `
            --account-name $intakeCosmosAccountName `
            --resource-group $resourceGroup `
            --output json | ConvertFrom-Json | Where-Object {
                $_.principalId -eq $searchPrincipalId -and
                $_.scope -eq $expectedCosmosScope -and
                $_.roleDefinitionId.EndsWith(
                    "/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001"
                )
            }
    )
    if ($LASTEXITCODE -ne 0 -or $assignments.Count -ne 1) {
        throw "Azure AI Search is missing its container-scoped Cosmos data-reader role."
    }
    $cosmosAccountReaderRole = "fbdf93bf-df7d-467e-a4d2-9458aa1360c8"
    $accountReaderAssignments = @(
        az role assignment list `
            --scope $intakeCosmosAccountId `
            --assignee-object-id $searchPrincipalId `
            --role $cosmosAccountReaderRole `
            --output json | ConvertFrom-Json
    )
    if (
        $LASTEXITCODE -ne 0 -or
        $accountReaderAssignments.Count -ne 1
    ) {
        throw "Azure AI Search is missing the Cosmos DB Account Reader Role."
    }

    $sharedLinks = az rest `
        --method get `
        --url (
            "https://management.azure.com$searchServiceId/" +
            "sharedPrivateLinkResources?api-version=2025-05-01"
        ) `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list Azure AI Search shared private links."
    }
    $cosmosLinks = @(
        $sharedLinks.value | Where-Object {
            $_.properties.privateLinkResourceId -eq $intakeCosmosAccountId -and
            $_.properties.groupId -eq "Sql" -and
            $_.properties.provisioningState -eq "Succeeded"
        }
    )
    if (
        $cosmosLinks.Count -ne 1 -or
        $cosmosLinks[0].properties.status -ne "Approved"
    ) {
        throw "The Search-to-intake-Cosmos shared private link is not approved."
    }

    $index = Get-SearchResource "indexes/$indexName"
    $fields = @{}
    foreach ($field in $index.fields) {
        $fields[$field.name] = $field
    }
    foreach ($fieldName in @("id", "tenantId", "createdBy", "status", "updatedAt", "searchTitle", "searchText", "searchVector")) {
        if (-not $fields.ContainsKey($fieldName)) {
            throw "Intake Search index is missing field '$fieldName'."
        }
    }
    if (
        -not $fields["tenantId"].filterable -or
        -not $fields["createdBy"].filterable -or
        -not $fields["status"].filterable -or
        $fields["searchVector"].dimensions -ne 3072
    ) {
        throw "Intake Search index filtering or vector configuration is invalid."
    }

    $dataSource = Get-SearchResource "datasources/$dataSourceName"
    if (
        $dataSource.type -ne "cosmosdb" -or
        $dataSource.container.name -ne $intakeCosmosContainerName -or
        $dataSource.dataChangeDetectionPolicy.highWaterMarkColumnName -ne "_ts"
    ) {
        throw "Intake Search Cosmos data source configuration is invalid."
    }

    $skillset = Get-SearchResource "skillsets/$skillsetName"
    $embeddingSkills = @(
        $skillset.skills | Where-Object {
            $_."@odata.type" -eq "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill"
        }
    )
    if (
        $embeddingSkills.Count -ne 1 -or
        $embeddingSkills[0].dimensions -ne 3072
    ) {
        throw "Intake Search embedding skill configuration is invalid."
    }

    $indexer = Get-SearchResource "indexers/$indexerName"
    if (
        $indexer.dataSourceName -ne $dataSourceName -or
        $indexer.targetIndexName -ne $indexName -or
        $indexer.skillsetName -ne $skillsetName -or
        $indexer.schedule.interval -ne $indexerInterval
    ) {
        throw "Intake Search indexer configuration is invalid."
    }

    $lastResult = $null
    for ($attempt = 1; $attempt -le $RetryCount; $attempt++) {
        $status = Get-SearchResource "indexers/$indexerName/status"
        $lastResult = $status.lastResult
        if ($lastResult.status -eq "success") {
            break
        }
        if ($lastResult.status -in @("transientFailure", "persistentFailure")) {
            throw (
                "Intake Search indexer failed: " +
                $lastResult.errorMessage
            )
        }
        if ($attempt -lt $RetryCount) {
            Start-Sleep -Seconds $RetryDelaySeconds
        }
    }
    if ($null -eq $lastResult -or $lastResult.status -ne "success") {
        throw "Intake Search indexer did not complete successfully."
    }
    if ([int]$lastResult.failedItemCount -gt 0) {
        throw "Intake Search indexer completed with failed items."
    }

    $documentCount = az rest `
        --method get `
        --url "$searchEndpoint/indexes/$indexName/docs/`$count?api-version=2025-09-01" `
        --resource "https://search.azure.com" `
        --output tsv
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read the intake Search document count."
    }

    Write-Output (
        "Intake Search validation succeeded. " +
        "Indexed document count: $documentCount."
    )
} finally {
    if ($null -eq $previousUserAgent) {
        Remove-Item Env:AZURE_DEV_USER_AGENT -ErrorAction SilentlyContinue
    } else {
        $env:AZURE_DEV_USER_AGENT = $previousUserAgent
    }
}
