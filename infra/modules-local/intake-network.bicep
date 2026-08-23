@description('Name of the existing workload VNet.')
param vnetName string

@description('Name of the dedicated Container Apps infrastructure subnet.')
param subnetName string = 'intake-container-apps-subnet'

@description('Address prefix for the dedicated Container Apps infrastructure subnet.')
param subnetPrefix string

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}

resource containerAppsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: subnetName
  properties: {
    addressPrefix: subnetPrefix
    delegations: [
      {
        name: 'Microsoft.App/environments'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
    privateEndpointNetworkPolicies: 'Enabled'
  }
}

output subnetId string = containerAppsSubnet.id
output subnetName string = containerAppsSubnet.name
