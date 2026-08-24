@description('Name of the existing Microsoft Foundry account.')
param accountName string

@description('Object ID of the Azure AI Search system-assigned managed identity.')
param searchPrincipalId string

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource cognitiveServicesUserRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  scope: subscription()
}

resource searchFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, searchPrincipalId, cognitiveServicesUserRole.id)
  scope: foundryAccount
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cognitiveServicesUserRole.id
  }
}

output roleAssignmentId string = searchFoundryUser.id
