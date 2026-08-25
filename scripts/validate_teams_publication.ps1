param(
    [string]$EnvironmentName = "maf-poc-byo",
    [string]$AgentName,
    [string]$BotServiceResourceId
)

$ErrorActionPreference = "Stop"

& "$PSScriptRoot\publish_teams.ps1" `
    -EnvironmentName $EnvironmentName `
    -AgentName $AgentName `
    -BotServiceResourceId $BotServiceResourceId `
    -ValidateOnly

if ($LASTEXITCODE -ne 0) {
    throw "Teams publication validation failed."
}
