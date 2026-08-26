@description('Name of the Azure AI Search service.')
param searchServiceName string

@description('Resource ID of the dedicated intake Cosmos DB account.')
param intakeCosmosAccountId string

@description('Deterministic suffix used in the shared private link name.')
param suffix string

resource searchService 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource cosmosSharedPrivateLink 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2025-05-01' = {
  parent: searchService
  name: 'intake-cosmos-${suffix}'
  properties: {
    groupId: 'Sql'
    privateLinkResourceId: intakeCosmosAccountId
    requestMessage: 'Approve private Cosmos DB access for intake search indexing.'
  }
}

output sharedPrivateLinkName string = cosmosSharedPrivateLink.name
