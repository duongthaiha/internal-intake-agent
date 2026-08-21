targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Microsoft Entra object ID that receives local data-plane access.')
param principalId string

@description('Foundry account name. Leave empty to generate a deterministic name.')
param foundryAccountName string = ''

@description('Foundry project name.')
param foundryProjectName string = 'maf-poc-project'

@description('Foundry model deployment name.')
param modelDeploymentName string = 'gpt-5.6-luna'

@description('Foundry model name.')
param modelName string = 'gpt-5.6-luna'

@description('Foundry model version.')
param modelVersion string = '2026-07-09'

@description('Foundry model deployment SKU.')
param modelSkuName string = 'GlobalStandard'

@description('Model deployment capacity in thousands of tokens per minute.')
@minValue(1)
param modelCapacity int = 500

@description('Cosmos DB account name. Leave empty to generate a deterministic name.')
param cosmosAccountName string = ''

@description('Cosmos DB SQL database name.')
param cosmosDatabaseName string = 'agent-framework'

@description('Cosmos DB container name.')
param cosmosContainerName string = 'chat-history'

@description('Cosmos DB SQL database for intake request records.')
param cosmosIntakeDatabaseName string = 'intake-agent'

@description('Cosmos DB container for intake request records.')
param cosmosIntakeContainerName string = 'requests'

@description('Azure AI Search service name. Leave empty to generate a deterministic name.')
param searchServiceName string = ''

@description('Azure AI Search index initialized by the hosted agent.')
param searchIndexName string = 'maf-poc-knowledge'

var suffix = substring(uniqueString(subscription().id, resourceGroup().id, 'maf-poc-private'), 0, 10)
var resolvedFoundryAccountName = empty(foundryAccountName) ? 'foundry-maf-${suffix}' : foundryAccountName
var resolvedCosmosAccountName = empty(cosmosAccountName) ? 'cosmos-maf-${suffix}' : cosmosAccountName
var resolvedSearchServiceName = empty(searchServiceName) ? 'srch-maf-${suffix}' : searchServiceName

module dataServices 'modules/data-services.bicep' = {
  name: 'private-data-services'
  params: {
    location: location
    principalId: principalId
    cosmosAccountName: resolvedCosmosAccountName
    cosmosDatabaseName: cosmosDatabaseName
    cosmosContainerName: cosmosContainerName
    cosmosIntakeDatabaseName: cosmosIntakeDatabaseName
    cosmosIntakeContainerName: cosmosIntakeContainerName
    searchServiceName: resolvedSearchServiceName
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'private-foundry'
  params: {
    location: location
    foundryAccountName: resolvedFoundryAccountName
    foundryProjectName: foundryProjectName
    modelDeploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    principalId: principalId
    cosmosAccountResourceId: dataServices.outputs.cosmosAccountResourceId
    searchServiceResourceId: dataServices.outputs.searchServiceResourceId
  }
}

output AZURE_AI_ACCOUNT_NAME string = foundry.outputs.foundryAccountName
output AZURE_AI_PROJECT_ID string = foundry.outputs.foundryProjectId
output AZURE_AI_PROJECT_NAME string = foundry.outputs.foundryProjectName
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = foundry.outputs.modelDeploymentName
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.foundryProjectEndpoint
output AZURE_COSMOS_ACCOUNT_NAME string = dataServices.outputs.cosmosAccountName
output AZURE_COSMOS_ENDPOINT string = dataServices.outputs.cosmosEndpoint
output AZURE_COSMOS_DATABASE_NAME string = dataServices.outputs.cosmosDatabaseName
output AZURE_COSMOS_CONTAINER_NAME string = dataServices.outputs.cosmosContainerName
output AZURE_COSMOS_INTAKE_DATABASE_NAME string = dataServices.outputs.cosmosIntakeDatabaseName
output AZURE_COSMOS_INTAKE_CONTAINER_NAME string = dataServices.outputs.cosmosIntakeContainerName
output AZURE_SEARCH_SERVICE_NAME string = dataServices.outputs.searchServiceName
output AZURE_SEARCH_ENDPOINT string = dataServices.outputs.searchEndpoint
output AZURE_SEARCH_INDEX_NAME string = searchIndexName
output ENABLE_HOSTED_AGENTS bool = true
