targetScope = 'resourceGroup'

@description('Name of an existing Azure Bot Service resource. This module never creates or updates it.')
param botServiceName string

resource botService 'Microsoft.BotService/botServices@2022-09-15' existing = {
  name: botServiceName
}

resource teamsChannel 'Microsoft.BotService/botServices/channels@2021-03-01' existing = {
  parent: botService
  name: 'MsTeamsChannel'
}

output resourceId string = botService.id
output name string = botService.name
output endpoint string = botService.properties.endpoint
output msaAppId string = botService.properties.msaAppId
output tenantId string = botService.properties.msaAppTenantId
output publicNetworkAccess string = botService.properties.publicNetworkAccess
output teamsChannelResourceId string = teamsChannel.id
