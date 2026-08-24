@description('Name of the Azure AI Search service.')
param searchServiceName string

@description('Resource ID of the dedicated Foundry IQ source storage account.')
param storageAccountId string

@description('Deterministic suffix used in shared private link names.')
param suffix string

@description('Object ID that provisions Foundry IQ Search data-plane resources.')
param provisionerPrincipalId string

resource searchService 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource searchServiceContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  scope: subscription()
}

resource searchIndexDataContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  scope: subscription()
}

resource provisionerSearchServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, provisionerPrincipalId, searchServiceContributorRole.id)
  scope: searchService
  properties: {
    principalId: provisionerPrincipalId
    roleDefinitionId: searchServiceContributorRole.id
  }
}

resource provisionerSearchIndexDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, provisionerPrincipalId, searchIndexDataContributorRole.id)
  scope: searchService
  properties: {
    principalId: provisionerPrincipalId
    roleDefinitionId: searchIndexDataContributorRole.id
  }
}

resource blobSharedPrivateLink 'Microsoft.Search/searchServices/sharedPrivateLinkResources@2025-05-01' = {
  parent: searchService
  name: 'foundry-iq-blob-${suffix}'
  properties: {
    groupId: 'blob'
    privateLinkResourceId: storageAccountId
    requestMessage: 'Approve private blob access for Foundry IQ ingestion.'
  }
}

output blobSharedPrivateLinkName string = blobSharedPrivateLink.name
