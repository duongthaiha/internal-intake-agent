@description('Azure region for the Foundry resources.')
param location string

param foundryAccountName string
param foundryProjectName string
param modelDeploymentName string
param modelName string
param modelVersion string
param modelSkuName string
param modelCapacity int
param principalId string
param cosmosAccountResourceId string
param searchServiceResourceId string

var networkConnectionApproverRoleId = 'b556d68e-0be0-4f35-a333-ad7ee1ce17ea'
var azureAiAdministratorRoleId = 'b78c5d69-af96-48a3-bf8d-a8b4d589de94'

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2026-05-01' = {
  name: foundryAccountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryAccountName
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Allow'
      ipRules: []
      virtualNetworkRules: []
    }
    networkInjections: [
      {
        scenario: 'agent'
        useMicrosoftManagedNetwork: true
      }
    ]
    publicNetworkAccess: 'Enabled'
  }
}

resource networkConnectionApproverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, networkConnectionApproverRoleId, resourceGroup().id)
  scope: resourceGroup()
  properties: {
    principalId: foundryAccount.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', networkConnectionApproverRoleId)
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2026-05-01' = {
  parent: foundryAccount
  name: modelDeploymentName
  sku: {
    capacity: modelCapacity
    name: modelSkuName
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2026-05-01' = {
  parent: foundryAccount
  name: foundryProjectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Microsoft Agent Framework POC with private Cosmos DB and Azure AI Search dependencies.'
    displayName: 'MAF POC private project'
  }
  dependsOn: [
    modelDeployment
    searchOutboundRule
  ]
}

resource azureAiAdministratorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, azureAiAdministratorRoleId)
  scope: project
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiAdministratorRoleId)
  }
}

resource managedNetwork 'Microsoft.CognitiveServices/accounts/managedNetworks@2026-05-15-preview' = {
  parent: foundryAccount
  name: 'default'
  properties: {
    managedNetwork: {
      isolationMode: 'AllowInternetOutbound'
      managedNetworkKind: 'V2'
    }
  }
  dependsOn: [
    networkConnectionApproverRole
  ]
}

resource cosmosOutboundRule 'Microsoft.CognitiveServices/accounts/managedNetworks/outboundRules@2026-05-15-preview' = {
  parent: managedNetwork
  name: 'cosmos-sql-rule'
  properties: {
    category: 'UserDefined'
    destination: {
      serviceResourceId: cosmosAccountResourceId
      subresourceTarget: 'Sql'
    }
    type: 'PrivateEndpoint'
  }
}

resource searchOutboundRule 'Microsoft.CognitiveServices/accounts/managedNetworks/outboundRules@2026-05-15-preview' = {
  parent: managedNetwork
  name: 'search-service-rule'
  properties: {
    category: 'UserDefined'
    destination: {
      serviceResourceId: searchServiceResourceId
      subresourceTarget: 'searchService'
    }
    type: 'PrivateEndpoint'
  }
  dependsOn: [
    cosmosOutboundRule
  ]
}

output foundryAccountName string = foundryAccount.name
output foundryProjectId string = project.id
output foundryProjectName string = project.name
output foundryProjectEndpoint string = 'https://${foundryAccount.name}.services.ai.azure.com/api/projects/${project.name}'
output modelDeploymentName string = modelDeployment.name
output managedNetworkName string = managedNetwork.name
