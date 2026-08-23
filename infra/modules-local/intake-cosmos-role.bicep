@description('Name of the dedicated intake Cosmos DB account.')
param accountName string

@description('Name of the intake Cosmos DB SQL database.')
param databaseName string

@description('Name of the intake Cosmos DB SQL container.')
param containerName string

@description('Principal ID of the Container App system-assigned managed identity.')
param principalId string

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
}

var dataContributorRoleDefinitionId = '00000000-0000-0000-0000-000000000002'
var containerScope = '${account.id}/dbs/${databaseName}/colls/${containerName}'

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, principalId, dataContributorRoleDefinitionId, containerScope)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleDefinitionId}'
    principalId: principalId
    scope: containerScope
  }
}

output roleAssignmentId string = roleAssignment.id
