// Repo-local addition (not part of upstream template 15, see infra/UPSTREAM.md).
// Creates the workload-specific chat-history database/container on the Cosmos DB
// account that standard-dependent-resources.bicep provisions for the Foundry
// Standard Agent setup. This is separate from the `enterprise_memory` database
// that the project capability host auto-provisions for agent thread storage.
// No data migration: this is a fresh container.

@description('Name of the existing Cosmos DB account (created by modules-network-secured/standard-dependent-resources.bicep).')
param cosmosAccountName string

@description('Name of the SQL database to create for the hosted agent chat history workload.')
param databaseName string = 'agent-framework'

@description('Name of the SQL container to create for the hosted agent chat history workload.')
param containerName string = 'chat-history'

@description('Partition key path for the chat-history container.')
param partitionKeyPath string = '/session_id'

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmosAccount
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
        kind: 'Hash'
        paths: [
          partitionKeyPath
        ]
        version: 2
      }
    }
  }
}

output databaseName string = database.name
output containerName string = container.name
