@description('Azure region for the API Management integration subnet resources.')
param location string

@description('Name of the existing workload virtual network.')
param vnetName string

@description('Name of the dedicated API Management outbound integration subnet.')
param subnetName string = 'apim-subnet'

@description('Address prefix for the dedicated API Management subnet.')
param subnetPrefix string

@description('Tags applied to the network security group.')
param resourceTags object = {}

resource networkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-${subnetName}'
  location: location
  tags: resourceTags
  properties: {
    securityRules: [
      {
        name: 'AllowHttpsToVirtualNetwork'
        properties: {
          priority: 100
          access: 'Allow'
          direction: 'Outbound'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: 'VirtualNetwork'
        }
      }
      {
        name: 'AllowAzureStorage'
        properties: {
          priority: 110
          access: 'Allow'
          direction: 'Outbound'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: 'Storage'
        }
      }
      {
        name: 'AllowAzureKeyVault'
        properties: {
          priority: 120
          access: 'Allow'
          direction: 'Outbound'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: 'AzureKeyVault'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}

resource apimSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: subnetName
  properties: {
    addressPrefix: subnetPrefix
    networkSecurityGroup: {
      id: networkSecurityGroup.id
    }
    delegations: [
      {
        name: 'Microsoft.Web/serverFarms'
        properties: {
          serviceName: 'Microsoft.Web/serverFarms'
        }
      }
    ]
    privateEndpointNetworkPolicies: 'Enabled'
  }
}

output subnetId string = apimSubnet.id
output networkSecurityGroupId string = networkSecurityGroup.id
