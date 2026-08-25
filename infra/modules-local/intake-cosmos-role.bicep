@description('Name of the dedicated intake Cosmos DB account.')
param accountName string

@description('Name of the intake Cosmos DB SQL database.')
param databaseName string

@description('Principal ID of the developer or operator that needs direct intake data access.')
param principalId string

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
}

var dataContributorRoleDefinitionId = '00000000-0000-0000-0000-000000000002'
var databaseScope = '${account.id}/dbs/${databaseName}'

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, principalId, dataContributorRoleDefinitionId, databaseScope)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleDefinitionId}'
    principalId: principalId
    scope: databaseScope
  }
}

output roleAssignmentId string = roleAssignment.id
