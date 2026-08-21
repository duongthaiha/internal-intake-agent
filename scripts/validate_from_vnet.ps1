<#
.SYNOPSIS
    Validates BYO-VNet private connectivity from inside the VNet. Intended to
    run on the Bastion-only Windows admin VM (AZURE_ADMIN_VM_NAME), reached via
    Azure Bastion (AZURE_BASTION_HOST_NAME), not from the developer machine.

.DESCRIPTION
    Resolves the DNS name of each private-endpoint-backed service, asserts the
    resolved address is a private RFC1918 address (proving the VM is using the
    private DNS zone, not the public endpoint), checks TCP/443 reachability,
    and (best-effort) invokes the hosted agent.

    Values are read from the azd environment when this repo/azd is available
    on the VM (pass -EnvironmentName and optionally -RepoPath); otherwise pass
    the explicit hostname/endpoint parameters directly. Explicit parameters
    always take precedence over azd-sourced values.

.EXAMPLE
    # On the admin VM, with the repo copied alongside this script and azd installed:
    .\validate_from_vnet.ps1 -EnvironmentName maf-poc-byo

.EXAMPLE
    # On the admin VM, with no azd/repo present, using explicit values:
    .\validate_from_vnet.ps1 `
        -FoundryProjectEndpoint "https://aifmafpoc1a2b.services.ai.azure.com/api/projects/maf-poc-project1a2b" `
        -CosmosEndpoint "https://aifmafpoc1a2bcosmosdb.documents.azure.com:443/" `
        -SearchEndpoint "https://aifmafpoc1a2bsearch.search.windows.net" `
        -StorageAccountName "aifmafpoc1a2bst"
#>
param(
    [string]$EnvironmentName = "maf-poc-byo",
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),

    [string]$FoundryProjectEndpoint,
    [string]$CosmosEndpoint,
    [string]$SearchEndpoint,
    [string]$StorageAccountName,
    [string]$AcrLoginServer,
    [string]$Location = "uksouth",

    [string]$AgentName = "maf-poc-agent",
    [string]$ModelDeploymentName,
    [string]$ExpectedSource = "maf-poc",
    [string]$Question = "Which model deployment does this POC use? Cite the source.",

    [int]$DnsTimeoutSeconds = 10,
    [int]$PortTimeoutSeconds = 10,

    [switch]$SkipAgentInvoke
)

$ErrorActionPreference = "Stop"

function Get-AzdValueFromRepo {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command azd -ErrorAction SilentlyContinue)) {
        return $null
    }
    if (-not (Test-Path (Join-Path $RepoPath "azure.yaml"))) {
        return $null
    }
    $value = azd env get-value $Name --environment $EnvironmentName --cwd $RepoPath 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        return $null
    }
    return $value.Trim()
}

function Get-EffectiveValue {
    param(
        [string]$Explicit,
        [Parameter(Mandatory)][string]$AzdName,
        [switch]$Required
    )

    if ($Explicit) {
        return $Explicit
    }
    $fromAzd = Get-AzdValueFromRepo -Name $AzdName
    if ($fromAzd) {
        return $fromAzd
    }
    if ($Required) {
        throw (
            "Could not resolve '$AzdName': no azd environment/repo was found at '$RepoPath' " +
            "for environment '$EnvironmentName', and no explicit override parameter was supplied. " +
            "Pass the corresponding -Parameter explicitly on this VM."
        )
    }
    return $null
}

function Get-HostNameFromEndpoint {
    param([Parameter(Mandatory)][string]$Endpoint)

    if ($Endpoint -match "^[a-zA-Z][a-zA-Z0-9+.-]*://") {
        return ([uri]$Endpoint).Host
    }
    return $Endpoint.Trim().TrimEnd("/")
}

function Test-Rfc1918Address {
    param([Parameter(Mandatory)][string]$IpAddress)

    $bytes = [System.Net.IPAddress]::Parse($IpAddress).GetAddressBytes()
    if ($bytes.Length -ne 4) {
        return $false
    }
    $first = [int]$bytes[0]
    $second = [int]$bytes[1]
    if ($first -eq 10) { return $true }
    if ($first -eq 172 -and $second -ge 16 -and $second -le 31) { return $true }
    if ($first -eq 192 -and $second -eq 168) { return $true }
    return $false
}

function Test-PrivateServiceEndpoint {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$HostName,
        [int]$Port = 443,
        [bool]$Required = $true
    )

    Write-Output "--- $Label ($HostName) ---"
    try {
        $dns = Resolve-DnsName -Name $HostName -Type A -QuickTimeout -ErrorAction Stop
    } catch {
        $message = "DNS resolution failed for '$HostName' ($Label): $($_.Exception.Message)"
        if ($Required) { throw $message }
        Write-Warning $message
        return [PSCustomObject]@{ Label = $Label; HostName = $HostName; Passed = $false; Required = $Required; Detail = $message }
    }

    $addresses = @($dns | Where-Object { $_.Type -eq "A" } | Select-Object -ExpandProperty IPAddress)
    if ($addresses.Count -eq 0) {
        $message = "'$HostName' ($Label) resolved to no A records."
        if ($Required) { throw $message }
        Write-Warning $message
        return [PSCustomObject]@{ Label = $Label; HostName = $HostName; Passed = $false; Required = $Required; Detail = $message }
    }

    $nonPrivate = @($addresses | Where-Object { -not (Test-Rfc1918Address $_) })
    if ($nonPrivate.Count -gt 0) {
        $message = (
            "'$HostName' ($Label) resolved to non-private address(es) [$($nonPrivate -join ', ')]. " +
            "Expected an RFC1918 address via the private DNS zone; this VM may be bypassing the " +
            "private endpoint (check VNet DNS settings and the private DNS zone link)."
        )
        if ($Required) { throw $message }
        Write-Warning $message
        return [PSCustomObject]@{ Label = $Label; HostName = $HostName; Passed = $false; Required = $Required; Detail = $message }
    }
    Write-Output "  DNS OK: $($addresses -join ', ') (private/RFC1918)"

    $connection = Test-NetConnection -ComputerName $HostName -Port $Port -WarningAction SilentlyContinue
    if (-not $connection.TcpTestSucceeded) {
        $message = "TCP/$Port to '$HostName' ($Label) failed."
        if ($Required) { throw $message }
        Write-Warning $message
        return [PSCustomObject]@{ Label = $Label; HostName = $HostName; Passed = $false; Required = $Required; Detail = $message }
    }
    Write-Output "  TCP/$Port OK"

    return [PSCustomObject]@{ Label = $Label; HostName = $HostName; Passed = $true; Required = $Required; Detail = "ok" }
}

# ---------------------------------------------------------------------------
# Resolve endpoints (explicit parameter, else azd env, else fail per-check)
# ---------------------------------------------------------------------------
$foundryEndpoint = Get-EffectiveValue -Explicit $FoundryProjectEndpoint -AzdName "FOUNDRY_PROJECT_ENDPOINT" -Required
$cosmosEndpoint = Get-EffectiveValue -Explicit $CosmosEndpoint -AzdName "AZURE_COSMOS_ENDPOINT" -Required
$searchEndpoint = Get-EffectiveValue -Explicit $SearchEndpoint -AzdName "AZURE_SEARCH_ENDPOINT" -Required
$storageAccountName = Get-EffectiveValue -Explicit $StorageAccountName -AzdName "AZURE_STORAGE_ACCOUNT_NAME" -Required
$acrLoginServer = Get-EffectiveValue -Explicit $AcrLoginServer -AzdName "AZURE_CONTAINER_REGISTRY_ENDPOINT"
if (-not $ModelDeploymentName) {
    $ModelDeploymentName = Get-EffectiveValue -Explicit $null -AzdName "AZURE_AI_MODEL_DEPLOYMENT_NAME"
}

$results = [System.Collections.Generic.List[object]]::new()

$results.Add((Test-PrivateServiceEndpoint -Label "Foundry account" -HostName (Get-HostNameFromEndpoint $foundryEndpoint)))
$results.Add((Test-PrivateServiceEndpoint -Label "Cosmos DB" -HostName (Get-HostNameFromEndpoint $cosmosEndpoint)))
$results.Add((Test-PrivateServiceEndpoint -Label "Azure AI Search" -HostName (Get-HostNameFromEndpoint $searchEndpoint)))
$results.Add((Test-PrivateServiceEndpoint -Label "Storage blob" -HostName "$storageAccountName.blob.core.windows.net"))
if ($acrLoginServer) {
    $results.Add((Test-PrivateServiceEndpoint -Label "Container Registry" -HostName (Get-HostNameFromEndpoint $acrLoginServer)))
}

# Azure Monitor / Application Insights private-link ingestion endpoints are
# global/regional (not resource-specific), so this is a best-effort check per
# Microsoft's AMPLS DNS documentation, not a hard requirement.
foreach ($monitorHost in @("global.in.ai.monitor.azure.com", "live.monitor.azure.com", "$Location.in.ai.monitor.azure.com")) {
    $results.Add((Test-PrivateServiceEndpoint -Label "Azure Monitor (AMPLS, best-effort)" -HostName $monitorHost -Required $false))
}

$failed = @($results | Where-Object { -not $_.Passed -and $_.Required })
if ($failed.Count -gt 0) {
    $summary = ($failed | ForEach-Object { "  - $($_.Label) ($($_.HostName)): $($_.Detail)" }) -join [Environment]::NewLine
    throw "One or more required private-connectivity checks failed:`n$summary"
}

# ---------------------------------------------------------------------------
# Hosted agent invoke (best-effort: requires azd + this repo on the VM)
# ---------------------------------------------------------------------------
if ($SkipAgentInvoke) {
    Write-Output "Skipping hosted agent invoke (-SkipAgentInvoke)."
} elseif (-not (Get-Command azd -ErrorAction SilentlyContinue) -or -not (Test-Path (Join-Path $RepoPath "azure.yaml"))) {
    Write-Warning (
        "Skipping hosted agent invoke: 'azd' and a copy of this repo (with azure.yaml) are " +
        "required on this VM to invoke by agent name. Install azd and copy the repo to " +
        "'$RepoPath' (or pass -RepoPath), or re-run with -SkipAgentInvoke to suppress this warning."
    )
} else {
    $response = azd ai agent invoke $AgentName $Question `
        --environment $EnvironmentName `
        --cwd $RepoPath `
        --new-session `
        --timeout 600 `
        --output default
    if ($LASTEXITCODE -ne 0 -or -not $response) {
        throw "Hosted agent invoke from inside the VNet failed."
    }
    $responseText = $response -join [Environment]::NewLine
    if ($ModelDeploymentName -and $responseText -notmatch [regex]::Escape($ModelDeploymentName)) {
        throw "The hosted agent response was not grounded with the model deployment name '$ModelDeploymentName'."
    }
    if ($responseText -notmatch [regex]::Escape($ExpectedSource)) {
        throw "The hosted agent response was not grounded with the expected knowledge source '$ExpectedSource'."
    }
    Write-Output "Hosted agent invoke from inside the VNet succeeded and the response was grounded."
}

Write-Output "All required private-connectivity checks from inside the VNet passed."
