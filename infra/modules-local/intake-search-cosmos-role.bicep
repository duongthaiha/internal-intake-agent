@description('Name of the dedicated intake Cosmos DB account.')
param accountName string

@description('Object ID of the Azure AI Search system-assigned managed identity.')
param searchPrincipalId string

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
}

resource cosmosAccountReaderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'fbdf93bf-df7d-467e-a4d2-9458aa1360c8'
  scope: subscription()
}

resource cosmosAccountReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, searchPrincipalId, cosmosAccountReaderRole.id)
  scope: account
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cosmosAccountReaderRole.id
  }
}

var dataReaderRoleDefinitionId = '00000000-0000-0000-0000-000000000001'

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, searchPrincipalId, dataReaderRoleDefinitionId)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataReaderRoleDefinitionId}'
    principalId: searchPrincipalId
    scope: account.id
  }
}

output roleAssignmentId string = roleAssignment.id
output accountReaderRoleAssignmentId string = cosmosAccountReaderAssignment.id
