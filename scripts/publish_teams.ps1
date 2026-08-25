<#
.SYNOPSIS
    Configures and publishes the hosted agent to Microsoft Teams and Microsoft 365.

.DESCRIPTION
    Reuses an existing Azure Bot Service resource. The script never creates a
    bot, Teams channel, app registration, or networking resource. It preserves
    the Responses and Entra endpoint configuration, adds Activity and
    BotServiceTenant, updates the existing bot endpoint, and publishes at
    Tenant scope.
#>
param(
    [string]$EnvironmentName = "maf-poc-byo",
    [string]$AgentName,
    [string]$BotServiceResourceId,
    [string]$MessagingEndpoint,
    [string]$DisplayName,
    [string]$AppVersion,
    [string]$ShortDescription,
    [string]$FullDescription,
    [string]$DeveloperName,
    [string]$DeveloperWebsiteUrl,
    [string]$PrivacyUrl,
    [string]$TermsOfUseUrl,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

function Get-AzdValueOrDefault {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$Default = ""
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

function Get-RequiredValue {
    param(
        [Parameter(Mandatory)][string]$Value,
        [Parameter(Mandatory)][string]$Name
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing required Teams publication value: $Name."
    }
    return $Value.Trim()
}

function Invoke-JsonCommand {
    param(
        [Parameter(Mandatory)][scriptblock]$Command,
        [Parameter(Mandatory)][string]$FailureMessage
    )

    $output = & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
    return ($output | ConvertFrom-Json)
}

function Invoke-TeamsContractHelper {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & python -m scripts.teams_publication @Arguments
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
        throw "Unable to build the Teams publication contract."
    }
    return ($output | Out-String).Trim()
}

function Get-AgentEndpointConfiguration {
    param([Parameter(Mandatory)][object]$Agent)

    if ($Agent.agent_endpoint) {
        return $Agent.agent_endpoint
    }
    if ($Agent.agentEndpoint) {
        return $Agent.agentEndpoint
    }
    throw "The Foundry agent response does not contain an agent endpoint configuration."
}

function Assert-AgentEndpointConfiguration {
    param([Parameter(Mandatory)][object]$Agent)

    $endpoint = Get-AgentEndpointConfiguration -Agent $Agent
    $protocols = if ($endpoint.protocol_configuration) {
        @($endpoint.protocol_configuration.PSObject.Properties.Name)
    } elseif ($endpoint.protocolConfiguration) {
        @($endpoint.protocolConfiguration.PSObject.Properties.Name)
    } elseif ($endpoint.protocols) {
        @($endpoint.protocols)
    } else {
        @()
    }
    foreach ($requiredProtocol in @("responses", "activity")) {
        if ($protocols -notcontains $requiredProtocol) {
            throw "Foundry agent endpoint is missing protocol '$requiredProtocol'."
        }
    }

    $schemes = if ($endpoint.authorization_schemes) {
        @($endpoint.authorization_schemes | ForEach-Object { $_.type })
    } elseif ($endpoint.authorizationSchemes) {
        @($endpoint.authorizationSchemes | ForEach-Object { $_.type })
    } else {
        @()
    }
    foreach ($requiredScheme in @("Entra", "BotServiceTenant")) {
        if ($schemes -notcontains $requiredScheme) {
            throw "Foundry agent endpoint is missing authorization scheme '$requiredScheme'."
        }
    }
}

foreach ($command in @("az", "azd", "python")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' was not found on PATH."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $projectEndpoint = Get-RequiredValue `
        -Value (Get-AzdValueOrDefault "FOUNDRY_PROJECT_ENDPOINT") `
        -Name "FOUNDRY_PROJECT_ENDPOINT"
    if ([string]::IsNullOrWhiteSpace($AgentName)) {
        $AgentName = Get-AzdValueOrDefault "AGENT_MAF_POC_AGENT_NAME" "maf-poc-agent"
    }
    if ([string]::IsNullOrWhiteSpace($BotServiceResourceId)) {
        $BotServiceResourceId = Get-AzdValueOrDefault "AZURE_TEAMS_BOT_SERVICE_RESOURCE_ID"
    }
    $BotServiceResourceId = Get-RequiredValue `
        -Value $BotServiceResourceId `
        -Name "AZURE_TEAMS_BOT_SERVICE_RESOURCE_ID"

    $botIdPattern = (
        '^/subscriptions/(?<subscription>[^/]+)/resourceGroups/(?<resourceGroup>[^/]+)/' +
        'providers/Microsoft\.BotService/botServices/(?<name>[^/]+)$'
    )
    if ($BotServiceResourceId -notmatch $botIdPattern) {
        throw "AZURE_TEAMS_BOT_SERVICE_RESOURCE_ID must identify Microsoft.BotService/botServices."
    }
    $botSubscriptionId = $Matches.subscription
    $botResourceGroup = $Matches.resourceGroup
    $botName = $Matches.name

    $tenantId = Get-RequiredValue `
        -Value (Get-AzdValueOrDefault "AZURE_TENANT_ID") `
        -Name "AZURE_TENANT_ID"
    $escapedAgentName = [Uri]::EscapeDataString($AgentName)
    $agentUrl = "$($projectEndpoint.TrimEnd('/'))/agents/$escapedAgentName`?api-version=v1"
    $activityEndpoint = Invoke-TeamsContractHelper -Arguments @(
        "activity-endpoint",
        "--project-endpoint", $projectEndpoint,
        "--agent-name", $AgentName
    )
    if ([string]::IsNullOrWhiteSpace($MessagingEndpoint)) {
        $MessagingEndpoint = Get-AzdValueOrDefault "TEAMS_MESSAGING_ENDPOINT"
    }
    if ([string]::IsNullOrWhiteSpace($MessagingEndpoint)) {
        $MessagingEndpoint = $activityEndpoint
    }
    $messagingUri = $null
    if (
        -not [Uri]::TryCreate($MessagingEndpoint, [UriKind]::Absolute, [ref]$messagingUri) -or
        $messagingUri.Scheme -ne "https"
    ) {
        throw "TEAMS_MESSAGING_ENDPOINT must be an absolute HTTPS URL."
    }

    $bot = Invoke-JsonCommand -FailureMessage "Unable to read existing Bot Service '$botName'." -Command {
        az bot show `
            --name $botName `
            --resource-group $botResourceGroup `
            --subscription $botSubscriptionId `
            --output json
    }
    az bot msteams show `
        --name $botName `
        --resource-group $botResourceGroup `
        --subscription $botSubscriptionId `
        --query id `
        --output tsv | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw (
            "The existing Bot Service '$botName' does not have a Microsoft Teams channel. " +
            "Create and publish it through the approved administrative flow before retrying."
        )
    }

    $agent = Invoke-JsonCommand -FailureMessage "Unable to read Foundry agent '$AgentName'." -Command {
        az rest `
            --method get `
            --url $agentUrl `
            --resource "https://ai.azure.com" `
            --headers "Foundry-Features=AgentEndpoints=V1Preview" `
            --output json
    }
    $agentPrincipalId = if ($agent.instance_identity.principal_id) {
        $agent.instance_identity.principal_id
    } elseif ($agent.instanceIdentity.principalId) {
        $agent.instanceIdentity.principalId
    } else {
        throw "Foundry agent '$AgentName' does not expose an instance identity principal ID."
    }
    if ($bot.properties.msaAppId -ne $agentPrincipalId) {
        throw (
            "Bot Service '$botName' msaAppId '$($bot.properties.msaAppId)' does not match " +
            "the Foundry agent principal ID '$agentPrincipalId'."
        )
    }
    if ($bot.properties.msaAppType -ne "SingleTenant") {
        throw (
            "Bot Service '$botName' msaAppType must be 'SingleTenant'; " +
            "found '$($bot.properties.msaAppType)'."
        )
    }
    if ([string]::IsNullOrWhiteSpace($bot.properties.msaAppTenantId)) {
        throw "Bot Service '$botName' does not expose msaAppTenantId."
    }
    if ($bot.properties.msaAppTenantId -ne $tenantId) {
        throw (
            "Bot Service '$botName' tenant '$($bot.properties.msaAppTenantId)' does not match " +
            "AZURE_TENANT_ID '$tenantId'."
        )
    }

    if ($ValidateOnly) {
        Assert-AgentEndpointConfiguration -Agent $agent
        if ($bot.properties.endpoint -ne $MessagingEndpoint) {
            throw (
                "Bot Service '$botName' endpoint must be '$MessagingEndpoint'; " +
                "found '$($bot.properties.endpoint)'."
            )
        }
        $titleId = Get-AzdValueOrDefault "TEAMS_TITLE_ID"
        if ([string]::IsNullOrWhiteSpace($titleId)) {
            throw (
                "Missing TEAMS_TITLE_ID in azd environment '$EnvironmentName'. " +
                "Run publish_teams.ps1 with a new TEAMS_APP_VERSION, or set the " +
                "known title ID for the existing publication."
            )
        }
        Write-Output (
            "Teams publication configuration is valid for agent '$AgentName' and " +
            "existing Bot Service '$botName' (title ID '$titleId'). " +
            "Network reachability is externally managed."
        )
        return
    }

    $endpointPatch = Invoke-TeamsContractHelper -Arguments @("endpoint-patch")
    $endpointPatchFile = [System.IO.Path]::GetTempFileName()
    try {
        Set-Content -Path $endpointPatchFile -Value $endpointPatch -Encoding utf8NoBOM
        Invoke-JsonCommand -FailureMessage "Unable to configure Responses and Activity on '$AgentName'." -Command {
            az rest `
                --method patch `
                --url $agentUrl `
                --resource "https://ai.azure.com" `
                --headers `
                    "Content-Type=application/merge-patch+json" `
                    "Foundry-Features=AgentEndpoints=V1Preview" `
                --body "@$endpointPatchFile" `
                --output json
        } | Out-Null
    } finally {
        Remove-Item -Path $endpointPatchFile -Force -ErrorAction SilentlyContinue
    }

    if ($bot.properties.endpoint -ne $MessagingEndpoint) {
        az bot update `
            --name $botName `
            --resource-group $botResourceGroup `
            --subscription $botSubscriptionId `
            --endpoint $MessagingEndpoint `
            --output none
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to update Bot Service '$botName' with the Activity endpoint."
        }
    }

    $DisplayName = Get-RequiredValue `
        -Value ($DisplayName ? $DisplayName : (Get-AzdValueOrDefault "TEAMS_AGENT_DISPLAY_NAME")) `
        -Name "TEAMS_AGENT_DISPLAY_NAME"
    $AppVersion = Get-RequiredValue `
        -Value ($AppVersion ? $AppVersion : (Get-AzdValueOrDefault "TEAMS_APP_VERSION")) `
        -Name "TEAMS_APP_VERSION"
    $ShortDescription = Get-RequiredValue `
        -Value ($ShortDescription ? $ShortDescription : (Get-AzdValueOrDefault "TEAMS_SHORT_DESCRIPTION")) `
        -Name "TEAMS_SHORT_DESCRIPTION"
    $FullDescription = Get-RequiredValue `
        -Value ($FullDescription ? $FullDescription : (Get-AzdValueOrDefault "TEAMS_FULL_DESCRIPTION")) `
        -Name "TEAMS_FULL_DESCRIPTION"
    $DeveloperName = Get-RequiredValue `
        -Value ($DeveloperName ? $DeveloperName : (Get-AzdValueOrDefault "TEAMS_DEVELOPER_NAME")) `
        -Name "TEAMS_DEVELOPER_NAME"
    if ([string]::IsNullOrWhiteSpace($DeveloperWebsiteUrl)) {
        $DeveloperWebsiteUrl = Get-AzdValueOrDefault "TEAMS_DEVELOPER_WEBSITE_URL"
    }
    if ([string]::IsNullOrWhiteSpace($PrivacyUrl)) {
        $PrivacyUrl = Get-AzdValueOrDefault "TEAMS_PRIVACY_URL"
    }
    if ([string]::IsNullOrWhiteSpace($TermsOfUseUrl)) {
        $TermsOfUseUrl = Get-AzdValueOrDefault "TEAMS_TERMS_OF_USE_URL"
    }

    $payloadArguments = @(
        "publish-payload",
        "--display-name", $DisplayName,
        "--bot-service-resource-id", $BotServiceResourceId,
        "--app-version", $AppVersion,
        "--short-description", $ShortDescription,
        "--full-description", $FullDescription,
        "--developer-name", $DeveloperName
    )
    if (-not [string]::IsNullOrWhiteSpace($DeveloperWebsiteUrl)) {
        $payloadArguments += @("--developer-website-url", $DeveloperWebsiteUrl)
    }
    if (-not [string]::IsNullOrWhiteSpace($PrivacyUrl)) {
        $payloadArguments += @("--privacy-url", $PrivacyUrl)
    }
    if (-not [string]::IsNullOrWhiteSpace($TermsOfUseUrl)) {
        $payloadArguments += @("--terms-of-use-url", $TermsOfUseUrl)
    }
    $publishPayload = Invoke-TeamsContractHelper -Arguments $payloadArguments
    $publishPayloadFile = [System.IO.Path]::GetTempFileName()
    $publishUrl = (
        "$($projectEndpoint.TrimEnd('/'))/agents/$escapedAgentName/" +
        "microsoft365/publish?api-version=v1"
    )
    try {
        Set-Content -Path $publishPayloadFile -Value $publishPayload -Encoding utf8NoBOM
        $publishOutput = & az rest `
            --method post `
            --url $publishUrl `
            --resource "https://ai.azure.com" `
            --headers "Content-Type=application/json" `
            --body "@$publishPayloadFile" `
            --output json 2>&1
        $publishExitCode = $LASTEXITCODE
        $publishText = ($publishOutput | Out-String).Trim()
        if ($publishExitCode -ne 0) {
            if ($publishText -match "(?i)version already exists") {
                $existingTitleId = Get-AzdValueOrDefault "TEAMS_TITLE_ID"
                if ([string]::IsNullOrWhiteSpace($existingTitleId)) {
                    throw (
                        "Teams app version '$AppVersion' is already published, but " +
                        "TEAMS_TITLE_ID is not recorded. Increment TEAMS_APP_VERSION " +
                        "to publish an update or set the known title ID."
                    )
                }
                Write-Output (
                    "Teams app version '$AppVersion' is already published. " +
                    "Title ID: $existingTitleId. Increment TEAMS_APP_VERSION to " +
                    "update user-visible metadata."
                )
            } else {
                throw "Microsoft 365 publication failed: $publishText"
            }
        } else {
            $publishResult = $publishText | ConvertFrom-Json
            $titleId = if ($publishResult.titleId) {
                $publishResult.titleId
            } elseif ($publishResult.title_id) {
                $publishResult.title_id
            } else {
                throw "Microsoft 365 publication succeeded without returning a title ID."
            }
            azd env set TEAMS_TITLE_ID $titleId --environment $EnvironmentName
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to persist TEAMS_TITLE_ID in azd environment '$EnvironmentName'."
            }
            Write-Output "Published Teams app version '$AppVersion' at Tenant scope. Title ID: $titleId"
        }
    } finally {
        Remove-Item -Path $publishPayloadFile -Force -ErrorAction SilentlyContinue
    }

    $updatedAgent = Invoke-JsonCommand -FailureMessage "Unable to verify Foundry agent '$AgentName'." -Command {
        az rest `
            --method get `
            --url $agentUrl `
            --resource "https://ai.azure.com" `
            --headers "Foundry-Features=AgentEndpoints=V1Preview" `
            --output json
    }
    Assert-AgentEndpointConfiguration -Agent $updatedAgent
    Write-Output (
        "Foundry endpoint and existing Bot Service are configured for Teams. " +
        "Microsoft 365 admin approval and externally managed network reachability remain required."
    )
} finally {
    Pop-Location
}
