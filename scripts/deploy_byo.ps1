<#
.SYNOPSIS
    Deploys the BYO-VNet (dual inbound path) Foundry hosted-agent stack end to end:
    preflight validation, infra provisioning, hosted-agent deployment/RBAC, and
    post-deploy validation.

.DESCRIPTION
    Replaces the old managed-network "private" deployment flow. The current
    infra/main.bicep keeps the Foundry account's private endpoint as the primary
    path but also allows a single allowlisted public client IPv4 CIDR (dual
    inbound path) instead of a managed network with outbound rules, so this
    script never calls `az cognitiveservices account managed-network *`.

    Optionally frees GlobalStandard model quota consumed by the previous
    "rg-maf-poc-private-uk" deployment (only when -DeleteOldResourceGroup is
    passed; exact-name guarded, never touches any other resource group).

.PARAMETER PreflightOnly
    Run every preflight check (commands, auth, providers, Bicep build, IP/CIDR
    resolution, quota report, name-collision checks, RG locks, and
    `azd provision --preview`) and then stop. No provisioning, deployment,
    deletion, or agent invocation is performed.

.PARAMETER DeleteOldResourceGroup
    Deletes the old resource group (must exactly equal the well-known former
    deployment name, regardless of what -OldResourceGroup is set to) to free
    GlobalStandard model quota, then polls quota until enough capacity is
    available. Destructive; never applies to any other resource group.
#>
param(
    [string]$EnvironmentName = "maf-poc-byo",
    [string]$SubscriptionId = "87e1a785-896b-4eb4-a214-47f67995133e",
    [string]$TenantId = "c214aaa8-7a43-441a-b501-f942c96f54a8",
    [string]$ResourceGroup = "rg-maf-poc-byo-uk",
    [string]$Location = "uksouth",

    [string]$AiServicesName = "aif-maf-poc",
    [string]$ProjectName = "maf-poc-project",
    [string]$ModelName = "gpt-5.6-sol",
    [string]$ModelFormat = "OpenAI",
    [string]$ModelVersion = "2026-07-09",
    [string]$ModelSkuName = "GlobalStandard",
    [int]$ModelCapacity = 250,

    [string]$IntakeEntraAudience = $env:INTAKE_ENTRA_AUDIENCE,
    [string]$IntakeApimPublisherEmail = $env:AZURE_INTAKE_APIM_PUBLISHER_EMAIL,

    [string]$TeamsBotServiceResourceId = $env:AZURE_TEAMS_BOT_SERVICE_RESOURCE_ID,
    [string]$TeamsMessagingEndpoint = $env:TEAMS_MESSAGING_ENDPOINT,
    [string]$TeamsAgentDisplayName = "Internal Intake Agent",
    [string]$TeamsAppVersion = "1.0.0",
    [string]$TeamsShortDescription = "Create and manage internal intake requests.",
    [string]$TeamsFullDescription = "Helps employees develop, review, and submit internal intake requests.",
    [string]$TeamsDeveloperName = "Internal Intake",
    [string]$TeamsDeveloperWebsiteUrl = "",
    [string]$TeamsPrivacyUrl = "",
    [string]$TeamsTermsOfUseUrl = "",

    [string]$AllowedClientIp = "85.210.10.0/24",
    [string]$IpDetectionEndpoint = $env:PUBLIC_IP_ECHO_ENDPOINT,

    [string]$AdminVmUsername = "azadmin",
    [System.Security.SecureString]$AdminVmPassword,

    [switch]$DeleteOldResourceGroup,
    [string]$OldResourceGroup = "rg-maf-poc-private-uk",
    [int]$OldResourceGroupDeleteTimeoutMinutes = 60,
    [int]$QuotaTimeoutMinutes = 60,
    [int]$QuotaPollIntervalSeconds = 30,

    [int]$RbacRetryCount = 5,
    [int]$RbacRetryDelaySeconds = 30,

    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$env:AZD_NON_INTERACTIVE = "true"

if ([string]::IsNullOrWhiteSpace($IntakeEntraAudience)) {
    throw "Set INTAKE_ENTRA_AUDIENCE or pass -IntakeEntraAudience with the Microsoft Entra application audience for the intake API."
}
if ([string]::IsNullOrWhiteSpace($IntakeApimPublisherEmail)) {
    throw "Set AZURE_INTAKE_APIM_PUBLISHER_EMAIL or pass -IntakeApimPublisherEmail with the API Management publisher email."
}

# The destructive delete path is only ever allowed to target this exact,
# well-known resource group name, no matter what -OldResourceGroup is set to.
$script:ExpectedOldResourceGroupName = "rg-maf-poc-private-uk"

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][scriptblock]$Command,
        [Parameter(Mandatory)][string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Get-AzdValue {
    param([Parameter(Mandatory)][string]$Name)

    $value = azd env get-value $Name --environment $EnvironmentName
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "Missing azd environment value '$Name' in '$EnvironmentName'."
    }
    return $value.Trim()
}

function Assert-ProvidersRegistered {
    param([Parameter(Mandatory)][string[]]$Namespaces)

    $notRegistered = @()
    foreach ($ns in $Namespaces) {
        $state = az provider show --namespace $ns --query registrationState --output tsv
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to query registration state for resource provider '$ns'."
        }
        if ($state -ne "Registered") {
            $notRegistered += $ns
        }
    }
    if ($notRegistered.Count -gt 0) {
        throw (
            "Required resource provider(s) not registered: $($notRegistered -join ', '). " +
            "Run 'az provider register --namespace <name> --wait' for each and retry."
        )
    }
}

function Assert-NoResourceGroupLocks {
    param([Parameter(Mandatory)][string]$ResourceGroupName)

    if ((az group exists --name $ResourceGroupName) -ne "true") {
        return
    }
    $locks = az lock list --resource-group $ResourceGroupName --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect locks on resource group '$ResourceGroupName'."
    }
    if ($locks.Count -gt 0) {
        $names = ($locks | ForEach-Object { "$($_.name) ($($_.level))" }) -join ", "
        throw "Resource group '$ResourceGroupName' has lock(s) that would block this operation: $names. Remove them first."
    }
}

function Assert-NoFoundryNameCollision {
    param(
        [Parameter(Mandatory)][string]$AiServicesPrefix,
        [Parameter(Mandatory)][string]$Location
    )

    $deleted = az cognitiveservices account list-deleted --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list soft-deleted Cognitive Services accounts."
    }
    $prefixLower = $AiServicesPrefix.ToLowerInvariant()
    $collisions = @(
        $deleted | Where-Object {
            $_.name.ToLowerInvariant().StartsWith($prefixLower) -and $_.location -eq $Location
        }
    )
    if ($collisions.Count -gt 0) {
        $names = ($collisions | ForEach-Object { $_.name }) -join ", "
        throw (
            "Soft-deleted Cognitive Services account(s) matching prefix '$AiServicesPrefix' " +
            "already exist in '$Location': $names. Purge them first " +
            "('az cognitiveservices account purge --location $Location --name <name> " +
            "--resource-group <rg>') or choose a different -AiServicesName."
        )
    }
}

function Test-Ipv4Format {
    param([Parameter(Mandatory)][string]$Value)

    if ($Value -notmatch '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$') {
        return $false
    }
    foreach ($octet in @($Matches[1], $Matches[2], $Matches[3], $Matches[4])) {
        if ([int]$octet -gt 255) {
            return $false
        }
    }
    return $true
}

function Test-PublicIpv4 {
    param([Parameter(Mandatory)][string]$IpAddress)

    if (-not (Test-Ipv4Format $IpAddress)) {
        return $false
    }
    $bytes = [System.Net.IPAddress]::Parse($IpAddress).GetAddressBytes()
    $first = [int]$bytes[0]
    $second = [int]$bytes[1]
    if ($first -eq 0) { return $false }                                          # "this network"
    if ($first -eq 10) { return $false }                                         # RFC1918
    if ($first -eq 127) { return $false }                                        # loopback
    if ($first -eq 169 -and $second -eq 254) { return $false }                    # link-local
    if ($first -eq 172 -and $second -ge 16 -and $second -le 31) { return $false } # RFC1918
    if ($first -eq 192 -and $second -eq 168) { return $false }                    # RFC1918
    if ($first -eq 192 -and $second -eq 0 -and [int]$bytes[2] -eq 2) { return $false } # TEST-NET-1
    if ($first -eq 198 -and $second -eq 51 -and [int]$bytes[2] -eq 100) { return $false } # TEST-NET-2
    if ($first -eq 203 -and $second -eq 0 -and [int]$bytes[2] -eq 113) { return $false } # TEST-NET-3
    if ($first -eq 100 -and $second -ge 64 -and $second -le 127) { return $false } # CGNAT
    if ($first -ge 224) { return $false }                                        # multicast/reserved
    if ($IpAddress -eq "255.255.255.255") { return $false }
    return $true
}

function Get-DetectedPublicClientIp {
    param(
        [Parameter(Mandatory)][string]$Endpoint,
        [int]$TimeoutSeconds = 10
    )

    try {
        $response = Invoke-WebRequest -Uri $Endpoint -UseBasicParsing -TimeoutSec $TimeoutSeconds
    } catch {
        Write-Warning "IP detection endpoint '$Endpoint' did not respond: $($_.Exception.Message)"
        return $null
    }

    $body = $response.Content.Trim()
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($body -split "`r?`n")) {
        if ($line -match '^\s*ip=(?<ip>[0-9.]+)\s*$') {
            $candidates.Add($Matches.ip)
        }
    }
    if ($candidates.Count -eq 0 -and (Test-Ipv4Format $body)) {
        $candidates.Add($body)
    }

    $validPublic = @($candidates | Where-Object { Test-PublicIpv4 $_ } | Select-Object -Unique)
    if ($validPublic.Count -ne 1) {
        Write-Warning (
            "IP detection endpoint '$Endpoint' did not return exactly one valid public IPv4 " +
            "address (found: $(@($validPublic).Count))."
        )
        return $null
    }
    return $validPublic[0]
}

function Resolve-AllowedClientIpCidr {
    param(
        [string]$AllowedClientIp,
        [string]$IpDetectionEndpoint
    )

    if ($AllowedClientIp) {
        $candidate = $AllowedClientIp.Trim()
        $ip = $candidate
        if ($candidate -eq "0.0.0.0/0") {
            throw "-AllowedClientIp cannot be 0.0.0.0/0. Supply a public client IPv4 address or a narrow CIDR."
        }
        $prefixLength = 32
        if ($candidate.Contains("/")) {
            $parts = $candidate.Split("/")
            if ($parts.Count -ne 2 -or
                -not [int]::TryParse($parts[1], [ref]$prefixLength) -or
                $prefixLength -lt 24 -or
                $prefixLength -gt 32) {
                throw (
                    "-AllowedClientIp must be a public IPv4 address or a /24-/32 CIDR " +
                    "(e.g. 203.0.113.5 or 203.0.113.0/24); got '$candidate'."
                )
            }
            $ip = $parts[0]
        }
        if (-not (Test-Ipv4Format $ip)) {
            throw "-AllowedClientIp '$candidate' is not a valid IPv4 address."
        }
        if (-not (Test-PublicIpv4 $ip)) {
            throw (
                "-AllowedClientIp '$candidate' is not a public IPv4 address (private, loopback, " +
                "link-local, CGNAT, and reserved ranges are rejected). Supply the deployer's " +
                "actual public client IPv4."
            )
        }
        return $prefixLength -eq 32 ? "$ip/32" : "$ip/$prefixLength"
    }

    if ([string]::IsNullOrWhiteSpace($IpDetectionEndpoint)) {
        throw (
            "No approved IP detection endpoint is configured. Re-run with " +
            "-AllowedClientIp <your-public-ipv4>, pass -IpDetectionEndpoint, or set " +
            "PUBLIC_IP_ECHO_ENDPOINT to an approved Microsoft/enterprise IP-echo endpoint."
        )
    }
    Write-Output "Detecting deployer public IPv4 via '$IpDetectionEndpoint'..."
    $detected = Get-DetectedPublicClientIp -Endpoint $IpDetectionEndpoint
    if (-not $detected) {
        throw (
            "Automatic public IPv4 detection failed, or did not resolve to exactly one valid " +
            "public IPv4 address. Re-run with -AllowedClientIp <your-public-ipv4> or point " +
            "-IpDetectionEndpoint at a validated Microsoft/enterprise IP-echo endpoint."
        )
    }
    Write-Output "Detected public IPv4: $detected"
    return "$detected/32"
}

function New-StrongPassword {
    param([int]$Length = 24)

    $upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    $lower = "abcdefghijkmnopqrstuvwxyz"
    $digits = "23456789"
    $special = "!@#$%^&*-_=+"
    $all = $upper + $lower + $digits + $special
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        function Get-RandomChar {
            param([string]$Set)
            $bytes = [byte[]]::new(4)
            $rng.GetBytes($bytes)
            $index = [System.BitConverter]::ToUInt32($bytes, 0) % $Set.Length
            return $Set[$index]
        }

        $passwordChars = [System.Collections.Generic.List[char]]::new()
        $passwordChars.Add((Get-RandomChar $upper))
        $passwordChars.Add((Get-RandomChar $lower))
        $passwordChars.Add((Get-RandomChar $digits))
        $passwordChars.Add((Get-RandomChar $special))
        for ($i = $passwordChars.Count; $i -lt $Length; $i++) {
            $passwordChars.Add((Get-RandomChar $all))
        }
        for ($i = $passwordChars.Count - 1; $i -gt 0; $i--) {
            $bytes = [byte[]]::new(4)
            $rng.GetBytes($bytes)
            $j = [System.BitConverter]::ToUInt32($bytes, 0) % ($i + 1)
            $tmp = $passwordChars[$i]
            $passwordChars[$i] = $passwordChars[$j]
            $passwordChars[$j] = $tmp
        }
        return -join $passwordChars
    } finally {
        $rng.Dispose()
    }
}

function Get-ModelQuota {
    param(
        [Parameter(Mandatory)][string]$Location,
        [Parameter(Mandatory)][string]$ModelSkuName,
        [Parameter(Mandatory)][string]$ModelName
    )

    $quotaName = "OpenAI.$ModelSkuName.$ModelName"
    $usage = az cognitiveservices usage list `
        --location $Location `
        --query "[?name.value=='$quotaName'] | [0]" `
        --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query model quota for '$quotaName' in '$Location'."
    }
    if (-not $usage) {
        throw "No quota record found for '$quotaName' in '$Location'."
    }
    [PSCustomObject]@{
        Name      = $quotaName
        Limit     = [double]$usage.limit
        Current   = [double]$usage.currentValue
        Available = [math]::Floor([double]$usage.limit - [double]$usage.currentValue)
    }
}

function Wait-ModelQuota {
    param(
        [Parameter(Mandatory)][string]$Location,
        [Parameter(Mandatory)][string]$ModelSkuName,
        [Parameter(Mandatory)][string]$ModelName,
        [Parameter(Mandatory)][int]$RequiredCapacity,
        [int]$TimeoutMinutes = 60,
        [int]$PollIntervalSeconds = 30
    )

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $maxAttempts = [math]::Max(1, [int][math]::Ceiling(($TimeoutMinutes * 60.0) / $PollIntervalSeconds))
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $quota = Get-ModelQuota -Location $Location -ModelSkuName $ModelSkuName -ModelName $ModelName
        Write-Output (
            "Quota check $attempt/$maxAttempts`: $($quota.Current)/$($quota.Limit) used, " +
            "$($quota.Available) available (need $RequiredCapacity)."
        )
        if ($quota.Available -ge $RequiredCapacity) {
            return $quota
        }
        if ($attempt -eq $maxAttempts -or (Get-Date) -ge $deadline) {
            throw (
                "Timed out after $TimeoutMinutes minute(s) waiting for $RequiredCapacity unit(s) " +
                "of '$ModelSkuName.$ModelName' quota in '$Location' (last available: $($quota.Available))."
            )
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }
}

function Wait-ResourceGroupDeleted {
    param(
        [Parameter(Mandatory)][string]$Name,
        [int]$TimeoutMinutes = 60,
        [int]$PollIntervalSeconds = 20
    )

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ((az group exists --name $Name) -eq "true") {
        if ((Get-Date) -ge $deadline) {
            throw "Timed out after $TimeoutMinutes minute(s) waiting for resource group '$Name' to finish deleting."
        }
        Write-Output "Waiting for resource group '$Name' deletion to complete..."
        Start-Sleep -Seconds $PollIntervalSeconds
    }
}

function Remove-OldModelDeploymentIfPresent {
    param(
        [Parameter(Mandatory)][string]$ResourceGroupName,
        [Parameter(Mandatory)][string]$ModelName
    )

    $accountsRaw = az cognitiveservices account list `
        --resource-group $ResourceGroupName `
        --query "[?kind=='AIServices'].name" `
        --output tsv
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list Cognitive Services accounts in '$ResourceGroupName'."
    }
    $accounts = @($accountsRaw -split "`r?`n" | Where-Object { $_ })
    foreach ($accountName in $accounts) {
        $deployment = az cognitiveservices account deployment show `
            --name $accountName `
            --resource-group $ResourceGroupName `
            --deployment-name $ModelName `
            --output json 2>$null | ConvertFrom-Json
        if ($LASTEXITCODE -eq 0 -and $deployment) {
            Write-Output "Deleting model deployment '$ModelName' on account '$accountName' in '$ResourceGroupName' to free quota..."
            az cognitiveservices account deployment delete `
                --name $accountName `
                --resource-group $ResourceGroupName `
                --deployment-name $ModelName `
                --output none
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to delete model deployment '$ModelName' on account '$accountName'."
            }
        }
    }
}

function Invoke-AzdProvisionPreview {
    param([Parameter(Mandatory)][string]$EnvironmentName)

    $output = & azd provision --environment $EnvironmentName --preview --no-prompt 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String)
    Write-Output $text
    if ($exitCode -eq 0) {
        return [PSCustomObject]@{ Succeeded = $true; IsQuotaError = $false }
    }
    $isQuota = $text -match "(?i)quota|insufficient.*capacity|exceeds the currently available"
    return [PSCustomObject]@{ Succeeded = $false; IsQuotaError = [bool]$isQuota }
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

Assert-Command "az"
Assert-Command "azd"
Assert-Command "docker"

Invoke-Checked { az account set --subscription $SubscriptionId } `
    "Unable to select Azure subscription '$SubscriptionId'."
$account = az account show --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $account.id -ne $SubscriptionId -or $account.tenantId -ne $TenantId) {
    throw "Run 'az login --tenant $TenantId' and select subscription '$SubscriptionId', then retry."
}

Invoke-Checked { azd auth login --check-status } `
    "Run 'azd auth login --tenant-id $TenantId' and retry."

Assert-ProvidersRegistered -Namespaces @(
    "Microsoft.CognitiveServices",
    "Microsoft.Search",
    "Microsoft.DocumentDB",
    "Microsoft.Storage",
    "Microsoft.ContainerRegistry",
    "Microsoft.Network",
    "Microsoft.App",
    "Microsoft.ApiManagement",
    "Microsoft.Web",
    "Microsoft.ContainerService",
    "Microsoft.Compute",
    "Microsoft.Insights",
    "Microsoft.OperationalInsights",
    "Microsoft.ManagedIdentity"
)
if (-not [string]::IsNullOrWhiteSpace($TeamsBotServiceResourceId)) {
    if (
        $TeamsBotServiceResourceId -notmatch
        '^/subscriptions/(?<subscription>[^/]+)/resourceGroups/[^/]+/providers/Microsoft\.BotService/botServices/[^/]+$'
    ) {
        throw "-TeamsBotServiceResourceId must identify Microsoft.BotService/botServices."
    }
    $teamsBotSubscriptionId = $Matches.subscription
    $botProviderState = az provider show `
        --subscription $teamsBotSubscriptionId `
        --namespace Microsoft.BotService `
        --query registrationState `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or $botProviderState -ne "Registered") {
        throw (
            "Microsoft.BotService must be registered in the existing bot's subscription " +
            "'$teamsBotSubscriptionId'."
        )
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$mainBicepPath = Join-Path $repoRoot "infra\main.bicep"
if (-not (Test-Path $mainBicepPath)) {
    throw "Could not find '$mainBicepPath'."
}
Invoke-Checked { az bicep build --file $mainBicepPath --stdout | Out-Null } `
    "Bicep validation of 'infra\main.bicep' failed."

Assert-NoResourceGroupLocks -ResourceGroupName $ResourceGroup
if ($DeleteOldResourceGroup) {
    Assert-NoResourceGroupLocks -ResourceGroupName $OldResourceGroup
}

Assert-NoFoundryNameCollision -AiServicesPrefix $AiServicesName -Location $Location

$allowedClientIpCidr = Resolve-AllowedClientIpCidr -AllowedClientIp $AllowedClientIp -IpDetectionEndpoint $IpDetectionEndpoint

$initialQuota = Get-ModelQuota -Location $Location -ModelSkuName $ModelSkuName -ModelName $ModelName
Write-Output (
    "Current '$ModelSkuName.$ModelName' quota in '$Location': " +
    "$($initialQuota.Current)/$($initialQuota.Limit) used, $($initialQuota.Available) available " +
    "(requesting $ModelCapacity)."
)

$principalId = az ad signed-in-user show --query id --output tsv
if ($LASTEXITCODE -ne 0 -or -not $principalId) {
    throw "Unable to resolve the signed-in user's Microsoft Entra object ID."
}

$generatedPassword = $false
if (-not $AdminVmPassword) {
    $plaintextPassword = New-StrongPassword
    $generatedPassword = $true
} else {
    $plaintextPassword = [System.Net.NetworkCredential]::new("", $AdminVmPassword).Password
}

Push-Location $repoRoot
try {
    $environments = @(azd env list --output json | ConvertFrom-Json)
    if ($environments.Name -contains $EnvironmentName) {
        Invoke-Checked { azd env select $EnvironmentName } `
            "Unable to select azd environment '$EnvironmentName'."
    } else {
        Invoke-Checked {
            azd env new $EnvironmentName `
                --subscription $SubscriptionId `
                --location $Location `
                --no-prompt
        } "Unable to create azd environment '$EnvironmentName'."
    }

    $settings = [ordered]@{
        AZURE_SUBSCRIPTION_ID        = $SubscriptionId
        AZURE_TENANT_ID              = $TenantId
        AZURE_RESOURCE_GROUP         = $ResourceGroup
        AZURE_LOCATION               = $Location
        AZURE_PRINCIPAL_ID           = $principalId
        AZURE_AI_SERVICES_NAME       = $AiServicesName
        AZURE_AI_PROJECT_BASE_NAME   = $ProjectName
        AZURE_AI_MODEL_NAME          = $ModelName
        AZURE_AI_MODEL_FORMAT        = $ModelFormat
        AZURE_AI_MODEL_VERSION       = $ModelVersion
        AZURE_AI_MODEL_SKU_NAME      = $ModelSkuName
        AZURE_AI_MODEL_CAPACITY      = $ModelCapacity
        AZURE_ALLOWED_CLIENT_IP_CIDR = $allowedClientIpCidr
        AZURE_ACR_DEVELOPER_IP_CIDR  = $allowedClientIpCidr
        AZURE_ADMIN_VM_USERNAME      = $AdminVmUsername
        INTAKE_ENTRA_TENANT_ID       = $TenantId
        INTAKE_ENTRA_AUDIENCE        = $IntakeEntraAudience
        AZURE_INTAKE_APIM_PUBLISHER_EMAIL = $IntakeApimPublisherEmail
        DEPENDENCY_STARTUP_CHECKS    = "false"
    }
    if (-not [string]::IsNullOrWhiteSpace($TeamsBotServiceResourceId)) {
        $settings.AZURE_TEAMS_BOT_SERVICE_RESOURCE_ID = $TeamsBotServiceResourceId
        $settings.TEAMS_MESSAGING_ENDPOINT = $TeamsMessagingEndpoint
        $settings.TEAMS_AGENT_DISPLAY_NAME = $TeamsAgentDisplayName
        $settings.TEAMS_APP_VERSION = $TeamsAppVersion
        $settings.TEAMS_SHORT_DESCRIPTION = $TeamsShortDescription
        $settings.TEAMS_FULL_DESCRIPTION = $TeamsFullDescription
        $settings.TEAMS_DEVELOPER_NAME = $TeamsDeveloperName
        $settings.TEAMS_DEVELOPER_WEBSITE_URL = $TeamsDeveloperWebsiteUrl
        $settings.TEAMS_PRIVACY_URL = $TeamsPrivacyUrl
        $settings.TEAMS_TERMS_OF_USE_URL = $TeamsTermsOfUseUrl
    }
    foreach ($setting in $settings.GetEnumerator()) {
        Invoke-Checked {
            azd env set $setting.Key ([string]$setting.Value) --environment $EnvironmentName
        } "Unable to set azd environment value '$($setting.Key)'."
    }
    Invoke-Checked {
        azd env set AZURE_ADMIN_VM_PASSWORD $plaintextPassword --environment $EnvironmentName
    } "Unable to set the admin VM password in the azd environment."
    $plaintextPassword = $null
    if ($generatedPassword) {
        Write-Output "A one-time admin VM password was generated and stored only in azd environment '$EnvironmentName' (AZURE_ADMIN_VM_PASSWORD). It is not printed here."
    }

    $preview = Invoke-AzdProvisionPreview -EnvironmentName $EnvironmentName
    if (-not $preview.Succeeded -and -not $preview.IsQuotaError) {
        throw "azd provision --preview failed for a non-quota (template/policy) reason. Review the output above and fix infra/parameters before retrying."
    }

    if ($PreflightOnly) {
        if ($preview.Succeeded) {
            Write-Output "Preflight OK: azd provision --preview succeeded. No provisioning, deployment, or deletion was performed (-PreflightOnly)."
        } else {
            Write-Warning "Preflight completed: azd provision --preview failed due to insufficient model quota. No provisioning, deployment, or deletion was performed (-PreflightOnly)."
        }
        return
    }

    if (-not $preview.Succeeded) {
        Write-Warning "azd provision --preview failed due to insufficient model quota."
        if (-not $DeleteOldResourceGroup) {
            throw (
                "Insufficient '$ModelSkuName.$ModelName' quota for the requested capacity " +
                "($ModelCapacity) in '$Location'. Re-run with -DeleteOldResourceGroup to free " +
                "capacity from '$script:ExpectedOldResourceGroupName', or lower -ModelCapacity."
            )
        }
    }
    # A successful preview is authoritative for idempotent redeployments. The
    # usage API reports zero *unallocated* quota once this environment already
    # owns the requested deployment, but updating that deployment needs no new
    # quota.

    # ---------------------------------------------------------------------
    # Destructive: free quota from the old resource group (guarded)
    # ---------------------------------------------------------------------
    if ($DeleteOldResourceGroup) {
        if ($OldResourceGroup -ne $script:ExpectedOldResourceGroupName) {
            throw (
                "Refusing to delete '$OldResourceGroup': -DeleteOldResourceGroup only ever " +
                "deletes the known former deployment resource group " +
                "'$script:ExpectedOldResourceGroupName'. Pass -OldResourceGroup " +
                "'$script:ExpectedOldResourceGroupName' (its default) or omit it."
            )
        }

        if ((az group exists --name $OldResourceGroup) -eq "true") {
            Remove-OldModelDeploymentIfPresent -ResourceGroupName $OldResourceGroup -ModelName $ModelName

            Write-Output "Deleting resource group '$OldResourceGroup' (exact-name guard matched)..."
            az group delete --name $OldResourceGroup --yes --no-wait
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to start deletion of resource group '$OldResourceGroup'."
            }
            Wait-ResourceGroupDeleted -Name $OldResourceGroup -TimeoutMinutes $OldResourceGroupDeleteTimeoutMinutes
            Write-Output "Resource group '$OldResourceGroup' deleted."
        } else {
            Write-Output "Resource group '$OldResourceGroup' does not exist; nothing to delete."
        }

        Wait-ModelQuota -Location $Location -ModelSkuName $ModelSkuName -ModelName $ModelName `
            -RequiredCapacity $ModelCapacity -TimeoutMinutes $QuotaTimeoutMinutes `
            -PollIntervalSeconds $QuotaPollIntervalSeconds | Out-Null
    }

    # ---------------------------------------------------------------------
    # Provision
    # ---------------------------------------------------------------------
    Invoke-Checked { azd provision --environment $EnvironmentName --no-prompt } `
        "BYO infrastructure provisioning failed."

    # ---------------------------------------------------------------------
    # Deploy hosted agent, assign RBAC, redeploy with checks enabled
    # ---------------------------------------------------------------------
    Invoke-Checked { azd deploy --environment $EnvironmentName --no-prompt } `
        "Bootstrap hosted-agent deployment failed."
    Invoke-Checked {
        & "$PSScriptRoot\assign_hosted_agent_roles.ps1" `
            -EnvironmentName $EnvironmentName `
            -ResourceGroup $ResourceGroup
    } "Hosted-agent role assignment failed."

    Invoke-Checked {
        azd env set DEPENDENCY_STARTUP_CHECKS true --environment $EnvironmentName
    } "Unable to enable hosted dependency startup checks."

    $deployed = $false
    for ($attempt = 1; $attempt -le $RbacRetryCount -and -not $deployed; $attempt++) {
        azd deploy --environment $EnvironmentName --no-prompt
        if ($LASTEXITCODE -eq 0) {
            $deployed = $true
            break
        }
        if ($attempt -lt $RbacRetryCount) {
            Write-Warning "Deployment attempt $attempt/$RbacRetryCount failed; waiting for RBAC propagation."
            Start-Sleep -Seconds $RbacRetryDelaySeconds
        }
    }
    if (-not $deployed) {
        throw "Hosted-agent deployment failed after $RbacRetryCount RBAC propagation retries."
    }

    Invoke-Checked {
        & "$PSScriptRoot\setup_intake_search.ps1" `
            -EnvironmentName $EnvironmentName
    } "Intake Cosmos to Azure AI Search setup failed."

    if (-not [string]::IsNullOrWhiteSpace($TeamsBotServiceResourceId)) {
        Invoke-Checked {
            & "$PSScriptRoot\publish_teams.ps1" `
                -EnvironmentName $EnvironmentName `
                -BotServiceResourceId $TeamsBotServiceResourceId
        } "Teams and Microsoft 365 publication failed."
    }

    # ---------------------------------------------------------------------
    # Validate
    # ---------------------------------------------------------------------
    Invoke-Checked {
        & "$PSScriptRoot\validate_byo_deployment.ps1" -EnvironmentName $EnvironmentName
    } "BYO deployment validation failed."

    Write-Output "BYO Foundry hosted-agent deployment completed successfully."
} finally {
    Pop-Location
}
