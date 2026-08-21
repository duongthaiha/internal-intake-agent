@description('Azure region for private data services.')
param location string

@description('Microsoft Entra object ID that receives local data-plane access.')
param principalId string

param cosmosAccountName string
param cosmosDatabaseName string
param cosmosContainerName string
param cosmosIntakeDatabaseName string
param cosmosIntakeContainerName string
param searchServiceName string

var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    locations: [
      {
        failoverPriority: 0
        isZoneRedundant: false
        locationName: location
      }
    ]
    minimalTlsVersion: 'Tls12'
    networkAclBypass: 'None'
    publicNetworkAccess: 'Disabled'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmosAccount
  name: cosmosDatabaseName
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: cosmosContainerName
  properties: {
    resource: {
      id: cosmosContainerName
      partitionKey: {
        kind: 'Hash'
        paths: [
          '/session_id'
        ]
        version: 2
      }
    }
  }
}

resource intakeDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmosAccount
  name: cosmosIntakeDatabaseName
  properties: {
    resource: {
      id: cosmosIntakeDatabaseName
    }
  }
}

resource intakeContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: intakeDatabase
  name: cosmosIntakeContainerName
  properties: {
    resource: {
      id: cosmosIntakeContainerName
      partitionKey: {
        kind: 'Hash'
        paths: [
          '/id'
        ]
        version: 2
      }
    }
  }
}

resource cosmosDataContributorRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' existing = {
  parent: cosmosAccount
  name: cosmosDataContributorRoleId
}

resource cosmosDataRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, principalId, cosmosDataContributorRoleId)
  properties: {
    principalId: principalId
    roleDefinitionId: cosmosDataContributorRole.id
    scope: cosmosAccount.id
  }
}

resource searchService 'Microsoft.Search/searchServices@2025-05-01' = {
  name: searchServiceName
  location: location
  sku: {
    name: 'basic'
  }
  properties: {
    disableLocalAuth: true
    hostingMode: 'Default'
    partitionCount: 1
    publicNetworkAccess: 'disabled'
    replicaCount: 1
    semanticSearch: 'free'
  }
}

resource searchServiceContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: searchServiceContributorRoleId
}

resource searchIndexDataContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: searchIndexDataContributorRoleId
}

resource searchIndexDataReaderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: searchIndexDataReaderRoleId
}

resource searchServiceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, principalId, searchServiceContributorRoleId)
  scope: searchService
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: searchServiceContributorRole.id
  }
}

resource searchIndexDataContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, principalId, searchIndexDataContributorRoleId)
  scope: searchService
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: searchIndexDataContributorRole.id
  }
}

resource searchIndexDataReaderRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, principalId, searchIndexDataReaderRoleId)
  scope: searchService
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: searchIndexDataReaderRole.id
  }
}

output cosmosAccountName string = cosmosAccount.name
output cosmosAccountResourceId string = cosmosAccount.id
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
output cosmosDatabaseName string = database.name
output cosmosContainerName string = container.name
output cosmosIntakeDatabaseName string = intakeDatabase.name
output cosmosIntakeContainerName string = intakeContainer.name
output searchServiceName string = searchService.name
output searchServiceResourceId string = searchService.id
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
