param(
    [string]$EnvironmentName = "maf-poc-byo",
    [string]$DocumentsPath = "data\knowledge",
    [int]$ValidationRetryCount = 20,
    [int]$ValidationRetryDelaySeconds = 30
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

$environmentVariables = @(
    "AZURE_SEARCH_ENDPOINT",
    "FOUNDRY_IQ_STORAGE_ACCOUNT_ID",
    "FOUNDRY_IQ_STORAGE_BLOB_ENDPOINT",
    "FOUNDRY_IQ_STORAGE_CONTAINER_NAME",
    "FOUNDRY_IQ_KNOWLEDGE_SOURCE_NAME",
    "FOUNDRY_IQ_KNOWLEDGE_BASE_NAME",
    "FOUNDRY_IQ_INGESTION_INTERVAL",
    "FOUNDRY_IQ_OPENAI_ENDPOINT",
    "FOUNDRY_IQ_EMBEDDING_DEPLOYMENT_NAME",
    "FOUNDRY_IQ_EMBEDDING_MODEL_NAME",
    "FOUNDRY_IQ_CHAT_DEPLOYMENT_NAME",
    "FOUNDRY_IQ_CHAT_MODEL_NAME"
)

foreach ($name in $environmentVariables) {
    Set-Item -Path "Env:$name" -Value (Get-AzdValue $name)
}

& "$PSScriptRoot\approve_foundry_iq_private_links.ps1" `
    -EnvironmentName $EnvironmentName
if ($LASTEXITCODE -ne 0) {
    throw "Foundry IQ private endpoint approval failed."
}

& "$PSScriptRoot\validate_foundry_iq_infrastructure.ps1" `
    -EnvironmentName $EnvironmentName
if ($LASTEXITCODE -ne 0) {
    throw "Foundry IQ infrastructure validation failed."
}

python -m scripts.upload_knowledge --documents $DocumentsPath
if ($LASTEXITCODE -ne 0) {
    throw "Foundry IQ Markdown upload failed."
}

python -m scripts.provision_foundry_iq
if ($LASTEXITCODE -ne 0) {
    throw "Foundry IQ knowledge source or knowledge base provisioning failed."
}

for ($attempt = 1; $attempt -le $ValidationRetryCount; $attempt++) {
    python -m scripts.validate_foundry_iq
    if ($LASTEXITCODE -eq 0) {
        Write-Output "Foundry IQ setup and ingestion validation succeeded."
        return
    }
    if ($attempt -eq $ValidationRetryCount) {
        break
    }
    Write-Warning (
        "Foundry IQ ingestion is not ready " +
        "(attempt $attempt/$ValidationRetryCount); retrying."
    )
    Start-Sleep -Seconds $ValidationRetryDelaySeconds
}

throw (
    "Foundry IQ validation did not return cited content after " +
    "$ValidationRetryCount attempt(s). Inspect the generated Search indexer."
)
