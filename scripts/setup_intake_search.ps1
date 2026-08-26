param(
    [string]$EnvironmentName = "maf-poc-byo",
    [switch]$SkipBackfill,
    [switch]$SkipIndexerRun
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

try {
    $environmentVariables = @(
        "AZURE_SEARCH_ENDPOINT",
        "INTAKE_COSMOS_ENDPOINT",
        "INTAKE_COSMOS_DATABASE_NAME",
        "INTAKE_COSMOS_CONTAINER_NAME",
        "FOUNDRY_IQ_OPENAI_ENDPOINT",
        "FOUNDRY_IQ_EMBEDDING_DEPLOYMENT_NAME",
        "FOUNDRY_IQ_EMBEDDING_MODEL_NAME"
    )

    foreach ($name in $environmentVariables) {
        Set-Item -Path "Env:$name" -Value (Get-AzdValue $name)
    }
    $resourceGroup = Get-AzdValue "AZURE_RESOURCE_GROUP"
    $intakeCosmosAccountName = Get-AzdValue "INTAKE_COSMOS_ACCOUNT_NAME"
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
    $env:INTAKE_COSMOS_ACCOUNT_RESOURCE_ID = $intakeCosmosAccountId.Trim()
    $env:INTAKE_SEARCH_INDEX_NAME = Get-AzdValueOrDefault `
        -Name "INTAKE_SEARCH_INDEX_NAME" `
        -Default "intake-requests"
    $env:INTAKE_SEARCH_INDEXER_INTERVAL = Get-AzdValueOrDefault `
        -Name "INTAKE_SEARCH_INDEXER_INTERVAL" `
        -Default "PT15M"

    & "$PSScriptRoot\approve_foundry_iq_private_links.ps1" `
        -EnvironmentName $EnvironmentName
    if ($LASTEXITCODE -ne 0) {
        throw "Search private endpoint approval failed."
    }

    if (-not $SkipBackfill) {
        python -m scripts.backfill_intake_search
        if ($LASTEXITCODE -ne 0) {
            throw "Intake Search projection backfill failed."
        }
    }

    $arguments = @("-m", "scripts.provision_intake_search")
    if (-not $SkipIndexerRun) {
        $arguments += "--run-indexer"
    }
    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Intake Azure AI Search provisioning failed."
    }

    if (-not $SkipIndexerRun) {
        & "$PSScriptRoot\validate_intake_search.ps1" `
            -EnvironmentName $EnvironmentName
        if ($LASTEXITCODE -ne 0) {
            throw "Intake Azure AI Search validation failed."
        }
    }
} finally {
    if ($null -eq $previousUserAgent) {
        Remove-Item Env:AZURE_DEV_USER_AGENT -ErrorAction SilentlyContinue
    } else {
        $env:AZURE_DEV_USER_AGENT = $previousUserAgent
    }
}

Write-Output "Intake Cosmos to Azure AI Search setup succeeded."
