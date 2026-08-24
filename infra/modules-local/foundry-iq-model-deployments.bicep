@description('Name of the existing Microsoft Foundry account.')
param accountName string

@description('Whether to deploy the Foundry IQ chat and embedding models.')
param deployModels bool = true

param chatDeploymentName string
param chatModelName string
param chatModelVersion string
param chatModelSkuName string
param chatModelCapacity int

param embeddingDeploymentName string
param embeddingModelName string
param embeddingModelVersion string
param embeddingModelSkuName string
param embeddingModelCapacity int

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = if (deployModels) {
  parent: account
  name: chatDeploymentName
  sku: {
    capacity: chatModelCapacity
    name: chatModelSkuName
  }
  properties: {
    model: {
      name: chatModelName
      format: 'OpenAI'
      version: chatModelVersion
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = if (deployModels) {
  parent: account
  name: embeddingDeploymentName
  sku: {
    capacity: embeddingModelCapacity
    name: embeddingModelSkuName
  }
  properties: {
    model: {
      name: embeddingModelName
      format: 'OpenAI'
      version: embeddingModelVersion
    }
  }
  dependsOn: [
    chatDeployment
  ]
}

output chatDeploymentName string = chatDeploymentName
output embeddingDeploymentName string = embeddingDeploymentName
