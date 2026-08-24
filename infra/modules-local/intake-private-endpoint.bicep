@description('Azure region for the Container Apps private endpoint.')
param location string

@description('Resource ID of the Container Apps managed environment.')
param managedEnvironmentId string

@description('Default domain of the Container Apps managed environment.')
param managedEnvironmentDefaultDomain string

@description('Resource ID of the subnet that hosts private endpoints.')
param privateEndpointSubnetId string

@description('Resource ID of the workload virtual network linked to private DNS.')
param virtualNetworkId string

@description('Tags applied to the private endpoint.')
param resourceTags object = {}

var privateDnsZoneName = 'privatelink.${location}.azurecontainerapps.io'
var privateEndpointName = 'intake-container-apps-private-endpoint'

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: privateDnsZoneName
  location: 'global'
}

resource privateDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: 'intake-container-apps-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetworkId
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: privateEndpointName
  location: location
  tags: resourceTags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${privateEndpointName}-connection'
        properties: {
          privateLinkServiceId: managedEnvironmentId
          groupIds: [
            'managedEnvironments'
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
        name: replace(managedEnvironmentDefaultDomain, '.', '-')
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
  dependsOn: [
    privateDnsVnetLink
  ]
}

output privateEndpointName string = privateEndpoint.name
output privateDnsZoneName string = privateDnsZone.name
