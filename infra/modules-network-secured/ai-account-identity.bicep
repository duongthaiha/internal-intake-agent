param accountName string
param location string
param modelName string
param modelFormat string
param modelVersion string
param modelSkuName string
param modelCapacity int
param agentSubnetId string
param networkInjection string = 'true'

// Dual inbound path (repo-local adaptation, see infra/UPSTREAM.md): the private
// endpoint created by private-endpoint-and-dns.bicep remains in place, but the
// account also allows public inbound from one narrow client IPv4 CIDR rule.
// Network ACL default action stays Deny so only the allowlisted IP (and private
// endpoint traffic) reach the account.
@description('Client IPv4 CIDR allowed to reach the Foundry account over its public endpoint. The private endpoint remains the primary path.')
param allowedClientIpCidr string
var allowedClientIp = replace(allowedClientIpCidr, '/32', '')

@description('Tags applied to the template-created Foundry account.')
param resourceTags object

// True BYO Foundry account.
// When existingAccountResourceId is set, reference the existing AI Foundry
// (Cognitive Services AIServices kind) account instead of creating a new one
// with a deterministic suffix (which orphans on re-runs and collides on conflict).
@description('Optional. Full ARM resource ID of an existing AI Foundry (CognitiveServices/accounts kind=AIServices) account to reuse. When set, the template will NOT create a new account.')
param existingAccountResourceId string = ''

@description('Optional. When true, skip the model deployment. Recommended when reusing an existing account that already has the required model deployments.')
param skipModelDeployment bool = false

var useExistingAccount = !empty(existingAccountResourceId)
var existingParts = split(existingAccountResourceId, '/')
var existingAccountSub = useExistingAccount ? existingParts[2] : subscription().subscriptionId
var existingAccountRg  = useExistingAccount ? existingParts[4] : resourceGroup().name
var existingAccountName = useExistingAccount ? last(existingParts) : accountName

#disable-next-line BCP036
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = if (!useExistingAccount) {
  name: accountName
  location: location
  tags: resourceTags
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    networkAcls: {
      defaultAction: 'Deny'
      virtualNetworkRules: []
      ipRules: [
        {
          // Cognitive Services represents a /32 rule as a bare IPv4.
          value: allowedClientIp
        }
      ]
      bypass:'AzureServices'
    }
    // Dual path: private endpoint stays authoritative, but public inbound is
    // enabled and immediately locked down to the single allowlisted client IP
    // via networkAcls above (defaultAction Deny).
    publicNetworkAccess: 'Enabled'
    networkInjections:((networkInjection == 'true') ? [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
      ] : null )
    disableLocalAuth: false
  }
}

// Reference to existing account (cross-RG / cross-sub aware)
resource existingAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: existingAccountName
  scope: resourceGroup(existingAccountSub, existingAccountRg)
}

#disable-next-line BCP081
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = if (!useExistingAccount && !skipModelDeployment) {
  parent: account
  name: modelName
  sku : {
    capacity: modelCapacity
    name: modelSkuName
  }
  properties: {
    model:{
      name: modelName
      format: modelFormat
      version: modelVersion
    }
  }
}

// Outputs use ARM short-circuit ternary so only the chosen branch is evaluated.
output accountName string = useExistingAccount ? existingAccount.name : account.name
output accountID string = useExistingAccount ? existingAccount.id : account.id
output accountTarget string = useExistingAccount ? existingAccount.properties.endpoint : account.properties.endpoint
output accountPrincipalId string = useExistingAccount ? existingAccount.identity.principalId : account.identity.principalId
