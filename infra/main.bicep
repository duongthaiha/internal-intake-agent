/*
Repo-local main.bicep for the Standard (network-secured) Agent Setup.

Adapted from the official Microsoft Foundry sample template 15
"private-network-standard-agent-setup" (commit 182cf1e2ecacc828e0fcb52c5a453f651dc44ac5).
See infra/UPSTREAM.md for the full list of repo-local adaptations on top of the
vendored infra/modules-network-secured/*.bicep modules.
*/
targetScope = 'resourceGroup'

@description('Location for all resources.')
@allowed([
  'westus'
  'eastus'
  'eastus2'
  'japaneast'
  'francecentral'
  'spaincentral'
  'uaenorth'
  'southcentralus'
  'italynorth'
  'germanywestcentral'
  'brazilsouth'
  'southafricanorth'
  'australiaeast'
  'swedencentral'
  'canadaeast'
  'canadacentral'
  'westeurope'
  'westus3'
  'uksouth'
  'southindia'

  //only class B and C
  'koreacentral'
  'polandcentral'
  'switzerlandnorth'
  'norwayeast'
])
param location string = 'uksouth'

@description('Microsoft Entra object ID of the deploying principal. Used for the admin VM "Virtual Machine Administrator Login" role assignment.')
param principalId string

@description('Name for your AI Services resource.')
param aiServices string = 'aif-maf-poc'

// Model deployment parameters
@description('The name of the model you want to deploy')
param modelName string = 'gpt-5.6-sol'
@description('The provider of your model')
param modelFormat string = 'OpenAI'
@description('The version of your model')
param modelVersion string = '2026-07-09'
@description('The sku of your model deployment')
param modelSkuName string = 'GlobalStandard'
@description('The tokens per minute (TPM) of your model deployment')
param modelCapacity int = 250

// Create a short, unique suffix, that will be unique to each resource group
// Deterministic suffix for idempotent re-deploys (same RG = same names)
var uniqueSuffix = substring(uniqueString(resourceGroup().id), 0, 4)
var accountName = toLower('${aiServices}${uniqueSuffix}')

@description('Name for your project resource.')
param firstProjectName string = 'maf-poc-project'

@description('This project will be a sub-resource of your account')
param projectDescription string = 'Microsoft Agent Framework POC project on the Standard (network secured) Agent Setup.'

@description('The display name of the project')
param displayName string = 'MAF POC network secured agent project'

// Existing Virtual Network parameters
@description('Virtual Network name. Required ONLY when creating a NEW VNet (existingVnetResourceId is empty). When existingVnetResourceId is set, this value is IGNORED, the name is derived from the resource ID.')
param vnetName string = 'maf-poc-vnet'

@description('The name of Agents Subnet to create new or existing subnet for agents')
param agentSubnetName string = 'agent-subnet'

@description('The name of Private Endpoint subnet to create new or existing subnet for private endpoints')
param peSubnetName string = 'pe-subnet'

//Existing standard Agent required resources
@description('Existing Virtual Network name Resource ID')
param existingVnetResourceId string = ''

@description('Address space for the VNet (only used for new VNet)')
param vnetAddressPrefix string = '192.168.0.0/16'

@description('Address prefix for the agent subnet.')
param agentSubnetPrefix string = '192.168.0.0/24'

@description('Address prefix for the private endpoint subnet')
param peSubnetPrefix string = '192.168.1.0/24'

// Non-destructive subnet handling.
@description('When true and existingVnetResourceId is set, the template will NOT modify your existing subnets.')
param reuseExistingSubnets bool = false

// True BYO Foundry account.
@description('Optional. Full ARM resource ID of an existing AI Foundry (CognitiveServices/accounts kind=AIServices) account to reuse. When set, the template will NOT create a new account.')
param existingAiFoundryAccountResourceId string = ''

@description('Optional. When true, skip the model deployment. Recommended when reusing an existing account that already has the required model deployments.')
param skipModelDeployment bool = false

@description('Enable Azure Container Registry with Private Endpoint. When true, creates an ACR (Premium SKU) with a PE in the private endpoints subnet.')
param enableContainerRegistry bool = true

@description('Optional developer IP CIDR to allowlist for ACR push access (e.g., 203.0.113.0/26 or 10.0.0.0/16). When empty, public access remains disabled (ACR stays private-only).')
param developerIpCidr string = ''

@description('Optional. Create the account-level capability host explicitly. Leave false for fresh deployments (the platform auto-creates {account}@aml_aiagentservice via networkInjections.scenario=agent). Set true only for a BYO account with no capability host, or to recreate after running deleteCapHost.sh.')
param createAccountCapabilityHost bool = false

// Re-derive BYO account context at main.bicep level so we can scope the
// account-level capabilityHost module to the right RG/subscription.
var useExistingAccount = !empty(existingAiFoundryAccountResourceId)
var existingAccountIdParts = split(existingAiFoundryAccountResourceId, '/')
var existingAccountSubscriptionId = useExistingAccount ? existingAccountIdParts[2] : subscription().subscriptionId
var existingAccountResourceGroupName = useExistingAccount ? existingAccountIdParts[4] : resourceGroup().name

@description('The AI Search Service full ARM Resource ID. This is an optional field, and if not provided, the resource will be created.')
param aiSearchResourceId string = ''
@description('The AI Storage Account full ARM Resource ID. This is an optional field, and if not provided, the resource will be created.')
param azureStorageAccountResourceId string = ''
@description('The Cosmos DB Account full ARM Resource ID. This is an optional field, and if not provided, the resource will be created.')
param azureCosmosDBAccountResourceId string = ''

@description('Subscription ID where existing private DNS zones are located. Leave empty to use current subscription.')
param dnsZonesSubscriptionId string = ''

@description('Object mapping DNS zone names to their resource group, or empty string to indicate creation')
param existingDnsZones object = {
  'privatelink.services.ai.azure.com': ''
  'privatelink.openai.azure.com': ''
  'privatelink.cognitiveservices.azure.com': ''
  'privatelink.search.windows.net': ''
  'privatelink.blob.core.windows.net': ''
  'privatelink.documents.azure.com': ''
  'privatelink.azurecr.io': ''
}

@description('Object mapping Azure Monitor private DNS zone names to the resource group of an existing zone, or empty string to create it.')
param existingMonitorDnsZones object = {
  'privatelink.monitor.azure.com': ''
  'privatelink.oms.opinsights.azure.com': ''
  'privatelink.ods.opinsights.azure.com': ''
  'privatelink.agentsvc.azure-automation.net': ''
}

@description('Zone Names for Validation of existing Private Dns Zones')
param dnsZoneNames array = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.search.windows.net'
  'privatelink.blob.core.windows.net'
  'privatelink.documents.azure.com'
  'privatelink.azurecr.io'
]

var projectName = toLower('${firstProjectName}${uniqueSuffix}')
// Sanitize aiServices for storage account name: lowercase, no hyphens, max 24 chars total.
var aiServicesSanitized = toLower(replace(aiServices, '-', ''))
var storagePrefixMax = 18 // 24 total - 4 (uniqueSuffix) - 2 ('st' marker)
var storagePrefix = length(aiServicesSanitized) > storagePrefixMax
  ? substring(aiServicesSanitized, 0, storagePrefixMax)
  : aiServicesSanitized
var azureStorageName = '${storagePrefix}${uniqueSuffix}st'

// Cosmos DB allows hyphens but enforces 44-char max. Cap defensively.
var cosmosDBNameRaw = toLower('${aiServices}${uniqueSuffix}cosmosdb')
var cosmosDBName = length(cosmosDBNameRaw) > 44 ? substring(cosmosDBNameRaw, 0, 44) : cosmosDBNameRaw

var aiSearchName = toLower('${aiServices}${uniqueSuffix}search')
var acrName = toLower('acr${uniqueSuffix}')

// Check if existing resources have been passed in
var storagePassedIn = azureStorageAccountResourceId != ''
var searchPassedIn = aiSearchResourceId != ''
var cosmosPassedIn = azureCosmosDBAccountResourceId != ''
var existingVnetPassedIn = existingVnetResourceId != ''

var acsParts = split(aiSearchResourceId, '/')
var aiSearchServiceSubscriptionId = searchPassedIn ? acsParts[2] : subscription().subscriptionId
var aiSearchServiceResourceGroupName = searchPassedIn ? acsParts[4] : resourceGroup().name

var cosmosParts = split(azureCosmosDBAccountResourceId, '/')
var cosmosDBSubscriptionId = cosmosPassedIn ? cosmosParts[2] : subscription().subscriptionId
var cosmosDBResourceGroupName = cosmosPassedIn ? cosmosParts[4] : resourceGroup().name

var storageParts = split(azureStorageAccountResourceId, '/')
var azureStorageSubscriptionId = storagePassedIn ? storageParts[2] : subscription().subscriptionId
var azureStorageResourceGroupName = storagePassedIn ? storageParts[4] : resourceGroup().name

var vnetParts = split(existingVnetResourceId, '/')
var vnetSubscriptionId = existingVnetPassedIn ? vnetParts[2] : subscription().subscriptionId
var vnetResourceGroupName = existingVnetPassedIn ? vnetParts[4] : resourceGroup().name
var existingVnetName = existingVnetPassedIn ? last(vnetParts) : vnetName
var trimVnetName = trim(existingVnetName)

// Resolve DNS zones subscription ID - use current subscription if not specified.
var trimmedDnsZonesSubscriptionId = trim(dnsZonesSubscriptionId)
var normalizedDnsZonesSubscriptionId = empty(trimmedDnsZonesSubscriptionId)
  ? ''
  : (startsWith(toLower(trimmedDnsZonesSubscriptionId), '/subscriptions/')
      ? trim(split(trimmedDnsZonesSubscriptionId, '/')[2])
      : trimmedDnsZonesSubscriptionId)
var resolvedDnsZonesSubscriptionId = empty(normalizedDnsZonesSubscriptionId) ? subscription().subscriptionId : normalizedDnsZonesSubscriptionId

@description('The name of the project capability host to be created')
param projectCapHost string = 'caphostproj'

// ---------------------------------------------------------------------------
// Repo-local additions (see infra/UPSTREAM.md)
// ---------------------------------------------------------------------------

@description('One narrow client IPv4 CIDR (/29-/32) allowed to reach the Foundry account over its public endpoint. The private endpoint remains the primary path; the account network ACL default action stays Deny.')
param allowedClientIpCidr string

@description('Azure AI Search index the hosted agent RAG pipeline initializes. Not created by this template; the hosted agent creates it at startup.')
param searchIndexName string = 'maf-poc-knowledge'

@description('Cosmos DB SQL database created for the hosted agent chat-history workload (separate from the capability host-managed enterprise_memory database).')
param cosmosWorkloadDatabaseName string = 'agent-framework'

@description('Cosmos DB SQL container created for the hosted agent chat-history workload.')
param cosmosWorkloadContainerName string = 'chat-history'

@description('Partition key path for the chat-history container.')
param cosmosWorkloadPartitionKeyPath string = '/session_id'

@description('Name of the admin subnet used for private admin access (Bastion + admin VM).')
param adminSubnetName string = 'admin-subnet'

@description('Deploy Azure Bastion and the private Windows administration VM. Disable when the subscription has no VM SKU capacity in the target region.')
param deployAdminAccess bool = false

@description('Address prefix for the admin subnet.')
param adminSubnetPrefix string = '192.168.2.0/24'

@description('Address prefix for the AzureBastionSubnet (name is fixed by the Azure Bastion service).')
param bastionSubnetPrefix string = '192.168.3.0/26'

@description('Local administrator username for the private admin VM.')
param adminUsername string = 'azadmin'

@description('Local administrator password for the private admin VM. Not emitted as an output.')
@secure()
param adminPassword string

// Create Virtual Network and Subnets
module vnet 'modules-network-secured/network-agent-vnet.bicep' = {
  name: 'vnet-${trimVnetName}-${uniqueSuffix}-deployment'
  params: {
    location: location
    vnetName: trimVnetName
    useExistingVnet: existingVnetPassedIn
    existingVnetResourceGroupName: vnetResourceGroupName
    agentSubnetName: agentSubnetName
    peSubnetName: peSubnetName
    vnetAddressPrefix: vnetAddressPrefix
    agentSubnetPrefix: agentSubnetPrefix
    peSubnetPrefix: peSubnetPrefix
    existingVnetSubscriptionId: vnetSubscriptionId
    reuseExistingSubnets: reuseExistingSubnets
  }
}

/*
  Create the AI Services account and model deployment
*/
module aiAccount 'modules-network-secured/ai-account-identity.bicep' = {
  name: '${accountName}-${uniqueSuffix}-deployment'
  params: {
    accountName: accountName
    location: location
    modelName: modelName
    modelFormat: modelFormat
    modelVersion: modelVersion
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    agentSubnetId: vnet.outputs.agentSubnetId
    existingAccountResourceId: existingAiFoundryAccountResourceId
    skipModelDeployment: skipModelDeployment
    allowedClientIpCidr: allowedClientIpCidr
  }
}

/*
  Validate existing resources
*/
module validateExistingResources 'modules-network-secured/validate-existing-resources.bicep' = {
  name: 'validate-existing-resources-${uniqueSuffix}-deployment'
  params: {
    aiSearchResourceId: aiSearchResourceId
    azureStorageAccountResourceId: azureStorageAccountResourceId
    azureCosmosDBAccountResourceId: azureCosmosDBAccountResourceId
    existingDnsZones: existingDnsZones
    dnsZoneNames: dnsZoneNames
    dnsZonesSubscriptionId: resolvedDnsZonesSubscriptionId
  }
}

module validateSearchAadAuth 'modules-network-secured/validate-search-aad-auth.bicep' = if (searchPassedIn) {
  name: 'validate-search-aad-auth-${uniqueSuffix}-deployment'
  params: {
    aiSearchName: last(acsParts)
    aiSearchResourceGroupName: aiSearchServiceResourceGroupName
    aiSearchSubscriptionId: aiSearchServiceSubscriptionId
  }
}

// This module will create new agent dependent resources
module aiDependencies 'modules-network-secured/standard-dependent-resources.bicep' = {
  name: 'dependencies-${uniqueSuffix}-deployment'
  params: {
    location: location
    azureStorageName: azureStorageName
    aiSearchName: aiSearchName
    cosmosDBName: cosmosDBName

    aiSearchResourceId: aiSearchResourceId
    aiSearchExists: validateExistingResources.outputs.aiSearchExists

    azureStorageAccountResourceId: azureStorageAccountResourceId
    azureStorageExists: validateExistingResources.outputs.azureStorageExists

    cosmosDBResourceId: azureCosmosDBAccountResourceId
    cosmosDBExists: validateExistingResources.outputs.cosmosDBExists
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2022-05-01' existing = {
  name: aiDependencies.outputs.azureStorageName
  scope: resourceGroup(azureStorageSubscriptionId, azureStorageResourceGroupName)
}

resource aiSearch 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: aiDependencies.outputs.aiSearchName
  scope: resourceGroup(aiDependencies.outputs.aiSearchServiceSubscriptionId, aiDependencies.outputs.aiSearchServiceResourceGroupName)
}

resource cosmosDB 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: aiDependencies.outputs.cosmosDBName
  scope: resourceGroup(cosmosDBSubscriptionId, cosmosDBResourceGroupName)
}

// Private Endpoint and DNS Configuration
module privateEndpointAndDNS 'modules-network-secured/private-endpoint-and-dns.bicep' = {
  name: '${uniqueSuffix}-private-endpoint'
  params: {
    aiAccountName: aiAccount.outputs.accountName
    location: location
    aiSearchName: aiDependencies.outputs.aiSearchName
    storageName: aiDependencies.outputs.azureStorageName
    cosmosDBName: aiDependencies.outputs.cosmosDBName
    vnetName: vnet.outputs.virtualNetworkName
    peSubnetName: vnet.outputs.peSubnetName
    suffix: uniqueSuffix
    vnetResourceGroupName: vnet.outputs.virtualNetworkResourceGroup
    vnetSubscriptionId: vnet.outputs.virtualNetworkSubscriptionId
    cosmosDBSubscriptionId: cosmosDBSubscriptionId
    cosmosDBResourceGroupName: cosmosDBResourceGroupName
    aiSearchSubscriptionId: aiSearchServiceSubscriptionId
    aiSearchResourceGroupName: aiSearchServiceResourceGroupName
    storageAccountResourceGroupName: azureStorageResourceGroupName
    storageAccountSubscriptionId: azureStorageSubscriptionId
    existingDnsZones: existingDnsZones
    dnsZonesSubscriptionId: resolvedDnsZonesSubscriptionId
  }
  dependsOn: [
    aiSearch
    storage
    cosmosDB
  ]
}

// Optional: Azure Container Registry with Private Endpoint. Public access stays
// disabled unless developerIpCidr is supplied.
module acr 'modules-network-secured/container-registry.bicep' = if (enableContainerRegistry) {
  name: 'acr-${uniqueSuffix}-deployment'
  params: {
    acrName: acrName
    location: location
    peSubnetId: vnet.outputs.peSubnetId
    vnetId: vnet.outputs.virtualNetworkId
    suffix: uniqueSuffix
    existingDnsZoneResourceGroup: existingDnsZones['privatelink.azurecr.io']
    dnsZonesSubscriptionId: resolvedDnsZonesSubscriptionId
    developerIpCidr: developerIpCidr
    projectPrincipalId: aiProject.outputs.projectPrincipalId
  }
  dependsOn: [
    privateEndpointAndDNS
  ]
}

// Application Insights for hosted-agent tracing.
module applicationInsights 'modules-network-secured/application-insights.bicep' = {
  name: 'app-insights-${uniqueSuffix}-deployment'
  params: {
    location: location
    suffix: uniqueSuffix
    aiAccountName: aiAccount.outputs.accountName
    disablePublicIngestion: true
  }
}

// Private trace ingestion path (Azure Monitor Private Link Scope).
module monitorPrivateLink 'modules-network-secured/monitor-private-link-scope.bicep' = {
  name: 'monitor-pls-${uniqueSuffix}-deployment'
  params: {
    location: location
    suffix: uniqueSuffix
    appInsightsId: applicationInsights.outputs.appInsightsId
    logAnalyticsId: applicationInsights.outputs.logAnalyticsId
    vnetId: vnet.outputs.virtualNetworkId
    peSubnetId: vnet.outputs.peSubnetId
    existingDnsZones: existingMonitorDnsZones
    dnsZonesSubscriptionId: resolvedDnsZonesSubscriptionId
  }
  dependsOn: [
    privateEndpointAndDNS
  ]
}

/*
  Creates a new project (sub-resource of the AI Services account)
*/
module aiProject 'modules-network-secured/ai-project-identity.bicep' = {
  name: '${projectName}-${uniqueSuffix}-deployment'
  params: {
    projectName: projectName
    projectDescription: projectDescription
    displayName: displayName
    location: location

    aiSearchName: aiDependencies.outputs.aiSearchName
    aiSearchServiceResourceGroupName: aiDependencies.outputs.aiSearchServiceResourceGroupName
    aiSearchServiceSubscriptionId: aiDependencies.outputs.aiSearchServiceSubscriptionId

    cosmosDBName: aiDependencies.outputs.cosmosDBName
    cosmosDBSubscriptionId: aiDependencies.outputs.cosmosDBSubscriptionId
    cosmosDBResourceGroupName: aiDependencies.outputs.cosmosDBResourceGroupName

    azureStorageName: aiDependencies.outputs.azureStorageName
    azureStorageSubscriptionId: aiDependencies.outputs.azureStorageSubscriptionId
    azureStorageResourceGroupName: aiDependencies.outputs.azureStorageResourceGroupName

    accountName: aiAccount.outputs.accountName
  }
  dependsOn: [
    validateSearchAadAuth
    privateEndpointAndDNS
    cosmosDB
    aiSearch
    storage
  ]
}

module formatProjectWorkspaceId 'modules-network-secured/format-project-workspace-id.bicep' = {
  name: 'format-project-workspace-id-${uniqueSuffix}-deployment'
  params: {
    projectWorkspaceId: aiProject.outputs.projectWorkspaceId
  }
}

// Native scheduled evaluations execute as the project managed identity.
module scheduledEvaluationRoleAssignment 'modules-local/foundry-scheduled-evaluation-role.bicep' = {
  name: 'scheduled-eval-ra-${uniqueSuffix}-deployment'
  params: {
    accountName: aiAccount.outputs.accountName
    projectName: aiProject.outputs.projectName
    projectPrincipalId: aiProject.outputs.projectPrincipalId
  }
}

module storageAccountRoleAssignment 'modules-network-secured/azure-storage-account-role-assignment.bicep' = {
  name: 'storage-${azureStorageName}-${uniqueSuffix}-deployment'
  scope: resourceGroup(azureStorageSubscriptionId, azureStorageResourceGroupName)
  params: {
    azureStorageName: aiDependencies.outputs.azureStorageName
    projectPrincipalId: aiProject.outputs.projectPrincipalId
  }
  dependsOn: [
    storage
    privateEndpointAndDNS
  ]
}

// The Cosmos DB Operator role must be assigned before the caphost is created
module cosmosAccountRoleAssignments 'modules-network-secured/cosmosdb-account-role-assignment.bicep' = {
  name: 'cosmos-account-ra-${uniqueSuffix}-deployment'
  scope: resourceGroup(cosmosDBSubscriptionId, cosmosDBResourceGroupName)
  params: {
    cosmosDBName: aiDependencies.outputs.cosmosDBName
    projectPrincipalId: aiProject.outputs.projectPrincipalId
  }
  dependsOn: [
    cosmosDB
    privateEndpointAndDNS
  ]
}

module aiSearchRoleAssignments 'modules-network-secured/ai-search-role-assignments.bicep' = {
  name: 'ai-search-ra-${uniqueSuffix}-deployment'
  scope: resourceGroup(aiSearchServiceSubscriptionId, aiSearchServiceResourceGroupName)
  params: {
    aiSearchName: aiDependencies.outputs.aiSearchName
    projectPrincipalId: aiProject.outputs.projectPrincipalId
  }
  dependsOn: [
    aiSearch
    privateEndpointAndDNS
  ]
}

module addAccountCapabilityHost 'modules-network-secured/add-account-capability-host.bicep' = if (createAccountCapabilityHost) {
  name: 'account-capability-host-${uniqueSuffix}-deployment'
  scope: resourceGroup(existingAccountSubscriptionId, existingAccountResourceGroupName)
  params: {
    accountName: aiAccount.outputs.accountName
    agentSubnetResourceId: vnet.outputs.agentSubnetId
  }
}

module addProjectCapabilityHost 'modules-network-secured/add-project-capability-host.bicep' = {
  name: 'capabilityHost-configuration-${uniqueSuffix}-deployment'
  params: {
    accountName: aiAccount.outputs.accountName
    projectName: aiProject.outputs.projectName
    cosmosDBConnection: aiProject.outputs.cosmosDBConnection
    azureStorageConnection: aiProject.outputs.azureStorageConnection
    aiSearchConnection: aiProject.outputs.aiSearchConnection
    projectCapHost: projectCapHost
  }
  dependsOn: [
    addAccountCapabilityHost
    aiSearch
    storage
    cosmosDB
    privateEndpointAndDNS
    cosmosAccountRoleAssignments
    storageAccountRoleAssignment
    aiSearchRoleAssignments
  ]
}

// The Storage Blob Data Owner role must be assigned after the caphost is created
module storageContainersRoleAssignment 'modules-network-secured/blob-storage-container-role-assignments.bicep' = {
  name: 'storage-containers-ra-${uniqueSuffix}-deployment'
  scope: resourceGroup(azureStorageSubscriptionId, azureStorageResourceGroupName)
  params: {
    aiProjectPrincipalId: aiProject.outputs.projectPrincipalId
    storageName: aiDependencies.outputs.azureStorageName
    workspaceId: formatProjectWorkspaceId.outputs.projectWorkspaceIdGuid
  }
  dependsOn: [
    addProjectCapabilityHost
  ]
}

// The Cosmos Built-In Data Contributor role must be assigned after the caphost is created
module cosmosContainerRoleAssignments 'modules-network-secured/cosmos-container-role-assignments.bicep' = {
  name: 'cosmos-containers-ra-${uniqueSuffix}-deployment'
  scope: resourceGroup(cosmosDBSubscriptionId, cosmosDBResourceGroupName)
  params: {
    cosmosAccountName: aiDependencies.outputs.cosmosDBName
    projectWorkspaceId: formatProjectWorkspaceId.outputs.projectWorkspaceIdGuid
    projectPrincipalId: aiProject.outputs.projectPrincipalId
  }
  dependsOn: [
    addProjectCapabilityHost
    storageContainersRoleAssignment
  ]
}

// Grant the project managed identity read access on the tracing Application Insights
module applicationInsightsRoleAssignment 'modules-network-secured/application-insights-role-assignment.bicep' = {
  name: 'app-insights-ra-${uniqueSuffix}-deployment'
  params: {
    appInsightsName: applicationInsights.outputs.appInsightsName
    projectPrincipalId: aiProject.outputs.projectPrincipalId
  }
}

// ---------------------------------------------------------------------------
// Repo-local additions (see infra/UPSTREAM.md)
// ---------------------------------------------------------------------------

// Workload-specific Cosmos DB database/container for hosted-agent chat history.
// Separate from the capability host's own `enterprise_memory` database. No data
// migration: this is a fresh container in a fresh deployment.
module workloadCosmosDatabase 'modules-local/workload-cosmos-database.bicep' = {
  name: 'workload-cosmos-db-${uniqueSuffix}-deployment'
  scope: resourceGroup(cosmosDBSubscriptionId, cosmosDBResourceGroupName)
  params: {
    cosmosAccountName: aiDependencies.outputs.cosmosDBName
    databaseName: cosmosWorkloadDatabaseName
    containerName: cosmosWorkloadContainerName
    partitionKeyPath: cosmosWorkloadPartitionKeyPath
  }
  dependsOn: [
    cosmosDB
  ]
}

// Private admin access: admin subnet + AzureBastionSubnet added to the VNet
// after its initial creation (non-racing, see modules-local/admin-access.bicep),
// a Standard Azure Bastion host with a Standard static public IP, and a Windows
// Server 2022 admin VM (no public IP, Entra ID login, Trusted Launch).
module adminAccess 'modules-local/admin-access.bicep' = if (deployAdminAccess) {
  name: 'admin-access-${uniqueSuffix}-deployment'
  scope: resourceGroup(vnetSubscriptionId, vnetResourceGroupName)
  params: {
    location: location
    vnetName: vnet.outputs.virtualNetworkName
    suffix: uniqueSuffix
    principalId: principalId
    adminSubnetName: adminSubnetName
    adminSubnetPrefix: adminSubnetPrefix
    bastionSubnetPrefix: bastionSubnetPrefix
    adminUsername: adminUsername
    adminPassword: adminPassword
  }
}

// ---------------------------------------------------------------------------
// Outputs (azd / hosted-agent scripts contract)
// ---------------------------------------------------------------------------

output AZURE_AI_ACCOUNT_NAME string = aiAccount.outputs.accountName
output AZURE_AI_PROJECT_ID string = aiProject.outputs.projectId
output AZURE_AI_PROJECT_NAME string = aiProject.outputs.projectName
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = modelName
output FOUNDRY_PROJECT_ENDPOINT string = 'https://${aiAccount.outputs.accountName}.services.ai.azure.com/api/projects/${aiProject.outputs.projectName}'

output AZURE_COSMOS_ACCOUNT_NAME string = aiDependencies.outputs.cosmosDBName
output AZURE_COSMOS_ENDPOINT string = 'https://${aiDependencies.outputs.cosmosDBName}.documents.azure.com:443/'
output AZURE_COSMOS_DATABASE_NAME string = workloadCosmosDatabase.outputs.databaseName
output AZURE_COSMOS_CONTAINER_NAME string = workloadCosmosDatabase.outputs.containerName

output AZURE_SEARCH_SERVICE_NAME string = aiDependencies.outputs.aiSearchName
output AZURE_SEARCH_ENDPOINT string = 'https://${aiDependencies.outputs.aiSearchName}.search.windows.net'
output AZURE_SEARCH_INDEX_NAME string = searchIndexName

output AZURE_STORAGE_ACCOUNT_NAME string = aiDependencies.outputs.azureStorageName

output AZURE_CONTAINER_REGISTRY_NAME string = enableContainerRegistry ? acr.outputs.acrName : ''
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = enableContainerRegistry ? acr.outputs.acrLoginServer : ''

output APPLICATIONINSIGHTS_CONNECTION_STRING string = applicationInsights.outputs.appInsightsConnectionString

output AZURE_VIRTUAL_NETWORK_NAME string = vnet.outputs.virtualNetworkName
output AZURE_AGENT_SUBNET_ID string = vnet.outputs.agentSubnetId
output AZURE_PE_SUBNET_ID string = vnet.outputs.peSubnetId
output AZURE_ADMIN_SUBNET_ID string = deployAdminAccess ? adminAccess.outputs.adminSubnetId : ''
output AZURE_BASTION_SUBNET_ID string = deployAdminAccess ? adminAccess.outputs.bastionSubnetId : ''

output AZURE_ADMIN_VM_NAME string = deployAdminAccess ? adminAccess.outputs.adminVmName : ''
output AZURE_BASTION_HOST_NAME string = deployAdminAccess ? adminAccess.outputs.bastionHostName : ''
output AZURE_BASTION_PUBLIC_IP string = deployAdminAccess ? adminAccess.outputs.bastionPublicIpAddress : ''

output ENABLE_HOSTED_AGENTS bool = true
output FOUNDRY_SCHEDULED_EVALUATION_ROLE_ASSIGNMENT_ID string = scheduledEvaluationRoleAssignment.outputs.roleAssignmentId
