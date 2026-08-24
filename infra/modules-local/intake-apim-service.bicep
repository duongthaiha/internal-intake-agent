@description('Azure region for API Management.')
param location string

@description('Globally unique API Management service name.')
param serviceName string

@description('API publisher display name.')
param publisherName string

@description('API publisher email address.')
param publisherEmail string

@description('Tags applied to API Management.')
param resourceTags object = {}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: serviceName
  location: location
  tags: resourceTags
  sku: {
    name: 'Developer'
    capacity: 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
    publicNetworkAccess: 'Enabled'
    virtualNetworkType: 'None'
  }
}

output serviceName string = apim.name
output publicIpAddress string = apim.properties.publicIPAddresses[0]
output gatewayUrl string = 'https://${apim.name}.azure-api.net'
