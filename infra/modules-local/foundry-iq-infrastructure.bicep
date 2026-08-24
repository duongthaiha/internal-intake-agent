@description('Azure region for the knowledge storage account and private endpoint.')
param location string

@description('Name of the dedicated Foundry IQ source storage account.')
param storageAccountName string

@description('Name of the private blob container that holds knowledge documents.')
param containerName string

@description('Resource ID of the subnet used for private endpoints.')
param privateEndpointSubnetId string

@description('Resource ID of the private DNS zone for blob storage.')
param blobPrivateDnsZoneId string

@description('Object ID of the Azure AI Search system-assigned managed identity.')
param searchPrincipalId string

@description('Object ID that uploads repository Markdown files to the knowledge container.')
param uploaderPrincipalId string

@description('Storage SKU for the dedicated knowledge account.')
param storageSkuName string = 'Standard_ZRS'

@description('Client IP CIDR allowed to access the storage account public endpoint.')
param allowedClientIpCidr string

@description('Tags applied to the dedicated Foundry IQ storage account.')
param resourceTags object

resource storageAccount 'Microsoft.Storage/storageAccounts@2025-01-01' = {
  name: storageAccountName
  location: location
  tags: resourceTags
  kind: 'StorageV2'
  sku: {
    name: storageSkuName
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
      ipRules: [
        {
          action: 'Allow'
          value: allowedClientIpCidr
        }
      ]
      virtualNetworkRules: []
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource knowledgeContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

resource knowledgePrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${storageAccountName}-private-endpoint'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-blob-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource knowledgePrivateDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: knowledgePrivateEndpoint
  name: '${storageAccountName}-dns-group'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: '${storageAccountName}-blob-dns'
        properties: {
          privateDnsZoneId: blobPrivateDnsZoneId
        }
      }
    ]
  }
}

resource storageBlobDataReaderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  scope: subscription()
}

resource storageBlobDataContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  scope: subscription()
}

resource searchStorageReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, searchPrincipalId, storageBlobDataReaderRole.id)
  scope: storageAccount
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataReaderRole.id
  }
}

resource uploaderStorageContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, uploaderPrincipalId, storageBlobDataContributorRole.id)
  scope: storageAccount
  properties: {
    principalId: uploaderPrincipalId
    roleDefinitionId: storageBlobDataContributorRole.id
  }
}

output storageAccountName string = storageAccount.name
output storageAccountId string = storageAccount.id
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output containerName string = knowledgeContainer.name
