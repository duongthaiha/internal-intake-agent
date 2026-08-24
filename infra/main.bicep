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

@description('Deployment name for the Foundry IQ answer-synthesis model.')
param foundryIqChatDeploymentName string = 'foundry-iq-chat'
@description('Model used by Foundry IQ for query planning and answer synthesis.')
param foundryIqChatModelName string = 'gpt-5.4-mini'
@description('Foundry IQ chat model version verified for UK South.')
param foundryIqChatModelVersion string = '2026-03-17'
@description('SKU for the Foundry IQ chat model deployment.')
param foundryIqChatModelSkuName string = 'GlobalStandard'
@description('Tokens per minute in thousands for the Foundry IQ chat model.')
param foundryIqChatModelCapacity int = 10

@description('Deployment name for the Foundry IQ embedding model.')
param foundryIqEmbeddingDeploymentName string = 'foundry-iq-embedding'
@description('Embedding model used by Foundry IQ ingestion.')
param foundryIqEmbeddingModelName string = 'text-embedding-3-large'
@description('Foundry IQ embedding model version verified for UK South.')
param foundryIqEmbeddingModelVersion string = '1'
@description('SKU for the Foundry IQ embedding model deployment.')
param foundryIqEmbeddingModelSkuName string = 'GlobalStandard'
@description('Tokens per minute in thousands for the Foundry IQ embedding model.')
param foundryIqEmbeddingModelCapacity int = 10

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

@description('Developer IP CIDR to allowlist for ACR push access. The private endpoint remains available and the network ACL default action stays Deny.')
param developerIpCidr string = '85.210.10.0/24'

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
var foundryIqStorageName = '${storagePrefix}${uniqueSuffix}iq'

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
var cosmosPrivateDnsZoneName = 'privatelink.documents.azure.com'
var cosmosPrivateDnsZoneResourceGroupName = empty(existingDnsZones[cosmosPrivateDnsZoneName])
  ? resourceGroup().name
  : existingDnsZones[cosmosPrivateDnsZoneName]
var cosmosPrivateDnsZoneId = resourceId(
  resolvedDnsZonesSubscriptionId,
  cosmosPrivateDnsZoneResourceGroupName,
  'Microsoft.Network/privateDnsZones',
  cosmosPrivateDnsZoneName
)
var blobPrivateDnsZoneName = 'privatelink.blob.${environment().suffixes.storage}'
var blobPrivateDnsZoneResourceGroupName = empty(existingDnsZones[blobPrivateDnsZoneName])
  ? resourceGroup().name
  : existingDnsZones[blobPrivateDnsZoneName]
var blobPrivateDnsZoneId = resourceId(
  resolvedDnsZonesSubscriptionId,
  blobPrivateDnsZoneResourceGroupName,
  'Microsoft.Network/privateDnsZones',
  blobPrivateDnsZoneName
)
var intakeCosmosAccountName = take(toLower('${aiServices}${uniqueSuffix}intake'), 44)
var intakeApimName = take(toLower('apim-${aiServices}-${uniqueSuffix}'), 50)

@description('The name of the project capability host to be created')
param projectCapHost string = 'caphostproj'

// ---------------------------------------------------------------------------
// Repo-local additions (see infra/UPSTREAM.md)
// ---------------------------------------------------------------------------

@description('One client IPv4 CIDR allowed through selected-network rules on template-created data services. Private endpoints remain the primary path and network ACLs stay deny-by-default.')
param allowedClientIpCidr string = '85.210.10.0/24'

var securityControlTags = {
  SecurityControl: 'Ignore'
}

@description('Azure AI Search index the hosted agent RAG pipeline initializes. Not created by this template; the hosted agent creates it at startup.')
param searchIndexName string = 'maf-poc-knowledge'

@description('Blob container that stores Markdown source documents for Foundry IQ ingestion.')
param foundryIqContainerName string = 'knowledge'

@description('Foundry IQ blob knowledge source name.')
param foundryIqKnowledgeSourceName string = 'ks-sop'

@description('Foundry IQ knowledge base name.')
param foundryIqKnowledgeBaseName string = 'sop-kb'

@description('ISO 8601 interval used for incremental Foundry IQ ingestion.')
param foundryIqIngestionInterval string = 'PT1H'

@description('Whether to create or update Foundry IQ Search shared private links.')
param manageFoundryIqSearchPrivateLinks bool = true

@description('Cosmos DB SQL database created for the hosted agent chat-history workload (separate from the capability host-managed enterprise_memory database).')
param cosmosWorkloadDatabaseName string = 'agent-framework'

@description('Cosmos DB SQL container created for the hosted agent chat-history workload.')
param cosmosWorkloadContainerName string = 'chat-history'

@description('Partition key path for the chat-history container.')
param cosmosWorkloadPartitionKeyPath string = '/session_id'

@description('Container image for the intake API. azd supplies this value after building the intake-api service.')
param intakeApiImageName string = ''

@description('Name of the dedicated intake Container Apps infrastructure subnet.')
param intakeContainerAppsSubnetName string = 'intake-container-apps-subnet'

@description('Address prefix for the dedicated intake Container Apps infrastructure subnet. It must not overlap any existing subnet.')
param intakeContainerAppsSubnetPrefix string = '192.168.4.0/23'

@description('Optional existing dedicated Container Apps infrastructure subnet resource ID. Supply this when the deployment must not modify an existing VNet.')
param intakeContainerAppsSubnetResourceId string = ''

@description('Publisher display name configured on the API Management service.')
param intakeApimPublisherName string = 'Internal Intake Platform'

@description('Publisher email configured on the API Management service.')
param intakeApimPublisherEmail string

@description('Maximum MCP tool calls allowed in each rate-limit window.')
@minValue(1)
param intakeMcpRateLimitCalls int = 60

@description('MCP rate-limit window in seconds.')
@minValue(1)
param intakeMcpRateLimitRenewalPeriod int = 60

@description('Name of the dedicated intake Cosmos DB SQL database.')
param intakeCosmosDatabaseName string = 'intake'

@description('Name of the dedicated intake Cosmos DB SQL container.')
param intakeCosmosContainerName string = 'intake-requests'

@description('Microsoft Entra tenant accepted by the intake API.')
param intakeEntraTenantId string = tenant().tenantId

@description('Microsoft Entra application audience accepted by the intake API.')
param intakeEntraAudience string

@description('Application role required for privileged intake reads.')
param intakePrivilegedReadRole string = 'Intake.Read.All'

@description('Application role required for privileged intake writes.')
param intakePrivilegedWriteRole string = 'Intake.ReadWrite.All'

@description('Delegated scope required for requester intake writes.')
param intakeDelegatedWriteScope string = 'Intake.ReadWrite'

@description('Minimum number of intake API replicas.')
@minValue(0)
param intakeApiMinReplicas int = 1

@description('Maximum number of intake API replicas.')
@minValue(1)
param intakeApiMaxReplicas int = 5

@description('Log level supplied to the intake API.')
param intakeApiLogLevel string = 'INFO'

@description('Create Foundry account-level scheduled evaluation role assignments. Set false when equivalent assignments already exist under different deterministic names.')
param createScheduledEvaluationAccountRoles bool = true

@description('Create or update the Foundry account private endpoint. Set false to preserve an existing endpoint during reprovisioning that also updates model deployments.')
param manageFoundryPrivateEndpoint bool = true

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
    containerAppsSubnetName: intakeContainerAppsSubnetName
    containerAppsSubnetPrefix: !existingVnetPassedIn || empty(intakeContainerAppsSubnetResourceId)
      ? intakeContainerAppsSubnetPrefix
      : ''
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
    resourceTags: securityControlTags
  }
}

module foundryIqModels 'modules-local/foundry-iq-model-deployments.bicep' = {
  name: 'foundry-iq-models-${uniqueSuffix}-deployment'
  scope: resourceGroup(existingAccountSubscriptionId, existingAccountResourceGroupName)
  params: {
    accountName: aiAccount.outputs.accountName
    deployModels: !skipModelDeployment
    chatDeploymentName: foundryIqChatDeploymentName
    chatModelName: foundryIqChatModelName
    chatModelVersion: foundryIqChatModelVersion
    chatModelSkuName: foundryIqChatModelSkuName
    chatModelCapacity: foundryIqChatModelCapacity
    embeddingDeploymentName: foundryIqEmbeddingDeploymentName
    embeddingModelName: foundryIqEmbeddingModelName
    embeddingModelVersion: foundryIqEmbeddingModelVersion
    embeddingModelSkuName: foundryIqEmbeddingModelSkuName
    embeddingModelCapacity: foundryIqEmbeddingModelCapacity
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
    allowedClientIpCidr: allowedClientIpCidr
    resourceTags: securityControlTags
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
    manageAiAccountPrivateEndpoint: manageFoundryPrivateEndpoint
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

// Optional Azure Container Registry with a private endpoint and selected-network
// public access for the configured developer CIDR.
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
    resourceTags: securityControlTags
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
  dependsOn: [
    foundryIqModels
  ]
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
    resourceTags: securityControlTags
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
    evaluationOperatorPrincipalId: principalId
    createAccountRoleAssignments: createScheduledEvaluationAccountRoles
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

module foundryIqInfrastructure 'modules-local/foundry-iq-infrastructure.bicep' = {
  name: 'foundry-iq-infrastructure-${uniqueSuffix}-deployment'
  params: {
    location: location
    storageAccountName: foundryIqStorageName
    containerName: foundryIqContainerName
    privateEndpointSubnetId: vnet.outputs.peSubnetId
    blobPrivateDnsZoneId: blobPrivateDnsZoneId
    searchPrincipalId: aiDependencies.outputs.aiSearchPrincipalId
    uploaderPrincipalId: principalId
    storageSkuName: contains(['southindia', 'westus'], location) ? 'Standard_GRS' : 'Standard_ZRS'
    allowedClientIpCidr: allowedClientIpCidr
    resourceTags: securityControlTags
  }
  dependsOn: [
    privateEndpointAndDNS
  ]
}

module foundryIqSearchPrivateLinks 'modules-local/foundry-iq-search-private-links.bicep' = if (manageFoundryIqSearchPrivateLinks) {
  name: 'foundry-iq-search-links-${uniqueSuffix}-deployment'
  scope: resourceGroup(aiSearchServiceSubscriptionId, aiSearchServiceResourceGroupName)
  params: {
    searchServiceName: aiDependencies.outputs.aiSearchName
    storageAccountId: foundryIqInfrastructure.outputs.storageAccountId
    suffix: uniqueSuffix
    provisionerPrincipalId: deployer().objectId
  }
}

module foundryIqFoundryRole 'modules-local/foundry-iq-foundry-role.bicep' = {
  name: 'foundry-iq-foundry-role-${uniqueSuffix}-deployment'
  scope: resourceGroup(existingAccountSubscriptionId, existingAccountResourceGroupName)
  params: {
    accountName: aiAccount.outputs.accountName
    searchPrincipalId: aiDependencies.outputs.aiSearchPrincipalId
  }
}

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

// Dedicated network and data plane for the intake API. These resources are kept
// separate from both the Foundry agent subnet and its chat-history Cosmos account.
module intakeNetwork 'modules-local/intake-network.bicep' = if (empty(intakeContainerAppsSubnetResourceId) && existingVnetPassedIn) {
  name: 'intake-network-${uniqueSuffix}-deployment'
  scope: resourceGroup(vnetSubscriptionId, vnetResourceGroupName)
  params: {
    vnetName: vnet.outputs.virtualNetworkName
    subnetName: intakeContainerAppsSubnetName
    subnetPrefix: intakeContainerAppsSubnetPrefix
  }
}

var resolvedIntakeContainerAppsSubnetId = !empty(intakeContainerAppsSubnetResourceId)
  ? intakeContainerAppsSubnetResourceId
  : (existingVnetPassedIn
      ? intakeNetwork!.outputs.subnetId
      : '${vnet.outputs.virtualNetworkId}/subnets/${intakeContainerAppsSubnetName}')

module intakeCosmos 'modules-local/intake-cosmos.bicep' = {
  name: 'intake-cosmos-${uniqueSuffix}-deployment'
  params: {
    location: location
    accountName: intakeCosmosAccountName
    databaseName: intakeCosmosDatabaseName
    containerName: intakeCosmosContainerName
    privateEndpointSubnetId: vnet.outputs.peSubnetId
    cosmosPrivateDnsZoneId: cosmosPrivateDnsZoneId
    allowedClientIpCidr: allowedClientIpCidr
    resourceTags: securityControlTags
  }
  dependsOn: [
    privateEndpointAndDNS
  ]
}

module intakeIdentity 'modules-local/intake-identity.bicep' = {
  name: 'intake-identity-${uniqueSuffix}-deployment'
  params: {
    location: location
    identityName: 'id-intake-${uniqueSuffix}'
    useContainerRegistry: enableContainerRegistry
    containerRegistryName: enableContainerRegistry ? acr!.outputs.acrName : ''
    cosmosAccountName: intakeCosmos.outputs.accountName
    cosmosDatabaseName: intakeCosmos.outputs.databaseName
    cosmosContainerName: intakeCosmos.outputs.containerName
  }
}

module intakeApimService 'modules-local/intake-apim-service.bicep' = {
  name: 'intake-apim-service-${uniqueSuffix}-deployment'
  params: {
    location: location
    serviceName: intakeApimName
    publisherName: intakeApimPublisherName
    publisherEmail: intakeApimPublisherEmail
    resourceTags: securityControlTags
  }
}

module intakeContainerApp 'modules-local/intake-container-app.bicep' = {
  name: 'intake-container-app-${uniqueSuffix}-deployment'
  params: {
    location: location
    imageName: intakeApiImageName
    infrastructureSubnetId: resolvedIntakeContainerAppsSubnetId
    useContainerRegistry: enableContainerRegistry
    containerRegistryLoginServer: enableContainerRegistry ? acr!.outputs.acrLoginServer : ''
    identityResourceId: intakeIdentity.outputs.identityId
    identityClientId: intakeIdentity.outputs.clientId
    cosmosEndpoint: intakeCosmos.outputs.endpoint
    cosmosDatabaseName: intakeCosmos.outputs.databaseName
    cosmosContainerName: intakeCosmos.outputs.containerName
    entraTenantId: intakeEntraTenantId
    entraAudience: intakeEntraAudience
    privilegedReadRole: intakePrivilegedReadRole
    privilegedWriteRole: intakePrivilegedWriteRole
    delegatedWriteScope: intakeDelegatedWriteScope
    applicationInsightsConnectionString: applicationInsights.outputs.appInsightsConnectionString
    logLevel: intakeApiLogLevel
    allowedIngressIpCidr: '${intakeApimService.outputs.publicIpAddress}/32'
    minReplicas: intakeApiMinReplicas
    maxReplicas: intakeApiMaxReplicas
  }
  dependsOn: [
    monitorPrivateLink
  ]
}

module intakeApimMcp 'modules-local/intake-apim-mcp.bicep' = {
  name: 'intake-apim-mcp-${uniqueSuffix}-deployment'
  params: {
    serviceName: intakeApimService.outputs.serviceName
    intakeBackendUrl: intakeContainerApp.outputs.uri
    entraTenantId: intakeEntraTenantId
    entraAudience: intakeEntraAudience
    rateLimitCalls: intakeMcpRateLimitCalls
    rateLimitRenewalPeriod: intakeMcpRateLimitRenewalPeriod
  }
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
output AZURE_AI_ACCOUNT_RESOURCE_ID string = aiAccount.outputs.accountID
output AZURE_AI_ACCOUNT_IS_EXISTING bool = useExistingAccount
output AZURE_AI_PROJECT_ID string = aiProject.outputs.projectId
output AZURE_AI_PROJECT_NAME string = aiProject.outputs.projectName
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = modelName
output FOUNDRY_PROJECT_ENDPOINT string = 'https://${aiAccount.outputs.accountName}.services.ai.azure.com/api/projects/${aiProject.outputs.projectName}'

output AZURE_COSMOS_ACCOUNT_NAME string = aiDependencies.outputs.cosmosDBName
output AZURE_COSMOS_ACCOUNT_IS_EXISTING bool = cosmosPassedIn
output AZURE_COSMOS_ENDPOINT string = 'https://${aiDependencies.outputs.cosmosDBName}.documents.azure.com:443/'
output AZURE_COSMOS_DATABASE_NAME string = workloadCosmosDatabase.outputs.databaseName
output AZURE_COSMOS_CONTAINER_NAME string = workloadCosmosDatabase.outputs.containerName

output AZURE_SEARCH_SERVICE_NAME string = aiDependencies.outputs.aiSearchName
output AZURE_SEARCH_SERVICE_RESOURCE_ID string = aiDependencies.outputs.aiSearchID
output AZURE_SEARCH_SERVICE_IS_EXISTING bool = searchPassedIn
output AZURE_SEARCH_ENDPOINT string = 'https://${aiDependencies.outputs.aiSearchName}.search.windows.net'
output AZURE_SEARCH_INDEX_NAME string = searchIndexName

output AZURE_STORAGE_ACCOUNT_NAME string = aiDependencies.outputs.azureStorageName
output AZURE_STORAGE_ACCOUNT_IS_EXISTING bool = storagePassedIn

output FOUNDRY_IQ_STORAGE_ACCOUNT_ID string = foundryIqInfrastructure.outputs.storageAccountId
output FOUNDRY_IQ_STORAGE_BLOB_ENDPOINT string = foundryIqInfrastructure.outputs.blobEndpoint
output FOUNDRY_IQ_STORAGE_CONTAINER_NAME string = foundryIqInfrastructure.outputs.containerName
output FOUNDRY_IQ_KNOWLEDGE_SOURCE_NAME string = foundryIqKnowledgeSourceName
output FOUNDRY_IQ_KNOWLEDGE_BASE_NAME string = foundryIqKnowledgeBaseName
output FOUNDRY_IQ_INGESTION_INTERVAL string = foundryIqIngestionInterval
output FOUNDRY_IQ_CHAT_DEPLOYMENT_NAME string = foundryIqModels.outputs.chatDeploymentName
output FOUNDRY_IQ_CHAT_MODEL_NAME string = foundryIqChatModelName
output FOUNDRY_IQ_EMBEDDING_DEPLOYMENT_NAME string = foundryIqModels.outputs.embeddingDeploymentName
output FOUNDRY_IQ_EMBEDDING_MODEL_NAME string = foundryIqEmbeddingModelName
output FOUNDRY_IQ_OPENAI_ENDPOINT string = 'https://${aiAccount.outputs.accountName}.openai.azure.com/'

output AZURE_CONTAINER_REGISTRY_NAME string = enableContainerRegistry ? acr!.outputs.acrName : ''
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = enableContainerRegistry ? acr!.outputs.acrLoginServer : ''

output INTAKE_COSMOS_ACCOUNT_NAME string = intakeCosmos.outputs.accountName
output INTAKE_COSMOS_ENDPOINT string = intakeCosmos.outputs.endpoint
output INTAKE_COSMOS_DATABASE_NAME string = intakeCosmos.outputs.databaseName
output INTAKE_COSMOS_CONTAINER_NAME string = intakeCosmos.outputs.containerName
output INTAKE_ENTRA_TENANT_ID string = intakeEntraTenantId
output INTAKE_ENTRA_AUDIENCE string = intakeEntraAudience
output INTAKE_PRIVILEGED_READ_ROLE string = intakePrivilegedReadRole
output INTAKE_PRIVILEGED_WRITE_ROLE string = intakePrivilegedWriteRole
output INTAKE_DELEGATED_WRITE_SCOPE string = intakeDelegatedWriteScope

output SERVICE_INTAKE_API_IMAGE_NAME string = intakeContainerApp.outputs.imageName
output SERVICE_INTAKE_API_NAME string = intakeContainerApp.outputs.appName
output SERVICE_INTAKE_API_URI string = intakeContainerApp.outputs.uri
output SERVICE_INTAKE_API_ENDPOINTS array = [
  intakeContainerApp.outputs.uri
]
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = intakeContainerApp.outputs.environmentName
output AZURE_INTAKE_CONTAINER_APPS_SUBNET_ID string = resolvedIntakeContainerAppsSubnetId
output INTAKE_COSMOS_ROLE_ASSIGNMENT_ID string = intakeIdentity.outputs.cosmosRoleAssignmentId
output AZURE_INTAKE_APIM_NAME string = intakeApimService.outputs.serviceName
output AZURE_INTAKE_MCP_SERVER_URL string = intakeApimMcp.outputs.mcpServerUrl

output APPLICATIONINSIGHTS_CONNECTION_STRING string = applicationInsights.outputs.appInsightsConnectionString
output APPLICATIONINSIGHTS_RESOURCE_ID string = applicationInsights.outputs.appInsightsId

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
output FOUNDRY_SCHEDULED_EVALUATION_MODEL_ROLE_ASSIGNMENT_ID string = scheduledEvaluationRoleAssignment.outputs.modelInferenceRoleAssignmentId
output FOUNDRY_EVALUATION_OPERATOR_MODEL_ROLE_ASSIGNMENT_ID string = scheduledEvaluationRoleAssignment.outputs.operatorModelInferenceRoleAssignmentId
