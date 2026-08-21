// Repo-local addition (not part of upstream template 15, see infra/UPSTREAM.md).
// Adds private admin access to the Standard Agent VNet: an admin subnet and the
// AzureBastionSubnet, a Standard Azure Bastion host, a Standard static public IP,
// a Windows Server 2022 VM with no public IP (Entra ID login via the
// AADLoginForWindows extension), and the "Virtual Machine Administrator Login"
// role assignment for the deploying principal.
//
// Subnets are added to the VNet created by modules-network-secured/vnet.bicep
// via the `Microsoft.Network/virtualNetworks/subnets` sub-resource type (the same
// technique modules-network-secured/subnet.bicep and existing-vnet.bicep use to
// add subnets to a VNet without re-PUTting its `subnets` array), so this never
// races with or replaces the official agent/PE subnets. Callers must depend on
// the vnet module output before invoking this module.

@description('Azure region for the admin access resources.')
param location string

@description('Name of the VNet created by modules-network-secured/vnet.bicep.')
param vnetName string

@description('Suffix for unique resource names (the template uniqueSuffix).')
param suffix string

@description('Microsoft Entra object ID granted the Virtual Machine Administrator Login role on the admin VM.')
param principalId string

@description('Name of the admin subnet.')
param adminSubnetName string = 'admin-subnet'

@description('Address prefix for the admin subnet.')
param adminSubnetPrefix string = '192.168.2.0/24'

@description('Address prefix for the AzureBastionSubnet (name is fixed by the Azure Bastion service).')
param bastionSubnetPrefix string = '192.168.3.0/26'

@description('Local administrator username for the admin VM.')
param adminUsername string

@description('Local administrator password for the admin VM.')
@secure()
param adminPassword string

var vmName = take('vm-admin-${suffix}', 15) // Windows computer name limit
var nicName = 'nic-admin-${suffix}'
var nsgName = 'nsg-admin-${suffix}'
var bastionName = 'bas-${suffix}'
var bastionPipName = 'pip-bas-${suffix}'

// Virtual Machine Administrator Login built-in role.
var vmAdminLoginRoleId = '1c0163c0-47e6-4577-8991-ea5c82e286e4'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}

// AzureBastionSubnet must use this exact name. No custom NSG here: Azure Bastion
// requires specific inbound/outbound rules and Microsoft recommends leaving the
// subnet unrestricted unless following the documented Bastion NSG rule set.
resource bastionSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  name: '${vnetName}/AzureBastionSubnet'
  properties: {
    addressPrefix: bastionSubnetPrefix
  }
}

// Admin subnet NSG: allow RDP only from the AzureBastionSubnet range, deny the
// rest of the VNet (which would otherwise reach the VM via the default
// AllowVnetInBound rule), and let the platform's default DenyAllInBound handle
// the internet.
resource adminNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'AllowRdpFromBastionSubnet'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '3389'
          sourceAddressPrefix: bastionSubnetPrefix
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'DenyOtherVnetInbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource adminSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  name: '${vnetName}/${adminSubnetName}'
  properties: {
    addressPrefix: adminSubnetPrefix
    networkSecurityGroup: {
      id: adminNsg.id
    }
  }
  dependsOn: [
    bastionSubnet
  ]
}

resource bastionPip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: bastionPipName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2024-05-01' = {
  name: bastionName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    ipConfigurations: [
      {
        name: 'bastionIpConfig'
        properties: {
          subnet: {
            id: bastionSubnet.id
          }
          publicIPAddress: {
            id: bastionPip.id
          }
        }
      }
    ]
  }
}

resource vmNic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: {
            id: adminSubnet.id
          }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

// Windows Server 2022 Datacenter: Azure Edition is a Gen2 image, required for
// Trusted Launch (Secure Boot + vTPM).
resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: vmName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: 'Standard_D2s_v5'
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      adminPassword: adminPassword
    }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftWindowsServer'
        offer: 'WindowsServer'
        sku: '2022-datacenter-azure-edition'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: vmNic.id
        }
      ]
    }
    // No public IP is attached to vmNic above; the VM is reachable only via
    // Azure Bastion (private admin access requirement).
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
  }
}

// Entra ID (Azure AD) login for Windows.
resource aadLoginExtension 'Microsoft.Compute/virtualMachines/extensions@2023-09-01' = {
  parent: vm
  name: 'AADLoginForWindows'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.ActiveDirectory'
    type: 'AADLoginForWindows'
    typeHandlerVersion: '2.2'
    autoUpgradeMinorVersion: true
  }
}

resource vmAdminLoginRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vm.id, principalId, vmAdminLoginRoleId)
  scope: vm
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', vmAdminLoginRoleId)
  }
  dependsOn: [
    aadLoginExtension
  ]
}

output adminSubnetId string = adminSubnet.id
output bastionSubnetId string = bastionSubnet.id
output adminVmName string = vm.name
output bastionHostName string = bastion.name
output bastionPublicIpAddress string = bastionPip.properties.ipAddress
