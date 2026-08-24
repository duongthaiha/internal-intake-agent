@description('Azure region for the dedicated intake Cosmos DB account.')
param location string

@description('Globally unique name of the dedicated intake Cosmos DB account.')
param accountName string

@description('Name of the intake Cosmos DB SQL database.')
param databaseName string

@description('Name of the intake Cosmos DB SQL container.')
param containerName string

@description('Resource ID of the existing private endpoint subnet.')
param privateEndpointSubnetId string

@description('Resource ID of the private DNS zone already linked to the workload VNet.')
param cosmosPrivateDnsZoneId string

@description('Client IPv4 CIDR allowed through the account selected-network rule.')
param allowedClientIpCidr string

@description('Tags applied to the Cosmos DB account.')
param resourceTags object

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  tags: resourceTags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    ipRules: [
      {
        ipAddressOrRange: allowedClientIpCidr
      }
    ]
    minimalTlsVersion: 'Tls12'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        kind: 'MultiHash'
        paths: [
          '/tenantId'
          '/id'
        ]
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
        ]
        compositeIndexes: [
          [
            {
              path: '/tenantId'
              order: 'ascending'
            }
            {
              path: '/updatedAt'
              order: 'descending'
            }
          ]
          [
            {
              path: '/tenantId'
              order: 'ascending'
            }
            {
              path: '/createdBy'
              order: 'ascending'
            }
            {
              path: '/updatedAt'
              order: 'descending'
            }
          ]
          [
            {
              path: '/tenantId'
              order: 'ascending'
            }
            {
              path: '/status'
              order: 'ascending'
            }
            {
              path: '/updatedAt'
              order: 'descending'
            }
          ]
          [
            {
              path: '/tenantId'
              order: 'ascending'
            }
            {
              path: '/createdBy'
              order: 'ascending'
            }
            {
              path: '/status'
              order: 'ascending'
            }
            {
              path: '/updatedAt'
              order: 'descending'
            }
          ]
        ]
      }
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${account.name}-private-endpoint'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${account.name}-sql-private-link'
        properties: {
          privateLinkServiceId: account.id
          groupIds: [
            'Sql'
          ]
        }
      }
    ]
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cosmos-sql'
        properties: {
          privateDnsZoneId: cosmosPrivateDnsZoneId
        }
      }
    ]
  }
}

output accountId string = account.id
output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
output containerName string = container.name
