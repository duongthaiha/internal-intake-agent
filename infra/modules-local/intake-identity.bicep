@description('Azure region for the intake API managed identity.')
param location string

@description('Name of the user-assigned managed identity.')
param identityName string

@description('Whether the deployment uses the provisioned Azure Container Registry.')
param useContainerRegistry bool

@description('Name of the provisioned Azure Container Registry.')
param containerRegistryName string

@description('Name of the dedicated intake Cosmos DB account.')
param cosmosAccountName string

@description('Name of the intake Cosmos DB SQL database.')
param cosmosDatabaseName string

@description('Name of the intake Cosmos DB SQL container.')
param cosmosContainerName string

var acrPullRoleDefinitionId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var cosmosDataContributorRoleDefinitionId = '00000000-0000-0000-0000-000000000002'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = if (useContainerRegistry) {
  name: containerRegistryName
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useContainerRegistry) {
  name: guid(registry.id, identity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleDefinitionId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}

var cosmosContainerScope = '${cosmosAccount.id}/dbs/${cosmosDatabaseName}/colls/${cosmosContainerName}'

resource cosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, identity.id, cosmosDataContributorRoleDefinitionId, cosmosContainerScope)
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributorRoleDefinitionId}'
    principalId: identity.properties.principalId
    scope: cosmosContainerScope
  }
}

output identityId string = identity.id
output clientId string = identity.properties.clientId
output principalId string = identity.properties.principalId
output cosmosRoleAssignmentId string = cosmosDataContributor.id
