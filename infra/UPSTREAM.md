# Upstream template provenance

This `infra/` directory is vendored and adapted from the official Microsoft
Foundry samples repository:

- Source: `foundry-samples/infrastructure/infrastructure-setup-bicep/15-private-network-standard-agent-setup`
- Commit: `182cf1e2ecacc828e0fcb52c5a453f651dc44ac5`

## What was vendored as-is

- `infra/modules-network-secured/*.bicep` — copied verbatim from the upstream
  `modules-network-secured/` folder, except for the two files noted below.

## Repo-local adaptations on top of upstream

1. **`infra/main.bicep`** is a repo-local top-level template based on the
   upstream `main.bicep` (same module wiring, dependency ordering, and
   capability-host sequencing), with:
   - Target defaults changed to this workload: `location=uksouth`,
     `firstProjectName=maf-poc-project`, `modelName=gpt-5.6-sol`,
     `modelFormat=OpenAI`, `modelVersion=2026-07-09`,
     `modelSkuName=GlobalStandard`, `modelCapacity=250`,
     `vnetAddressPrefix=192.168.0.0/16`, `agentSubnetPrefix=192.168.0.0/24`,
     `peSubnetPrefix=192.168.1.0/24`.
   - A preserved top-level `principalId` parameter (used only for the new
     admin VM role assignment described below; the upstream template has no
     equivalent parameter).
   - New parameters and modules for the Cosmos DB workload database, the
     Foundry dual inbound path, and private admin access (see below).
   - An `output` section (upstream `main.bicep` has none) exposing the
     azd/script-compatible contract: `AZURE_AI_ACCOUNT_NAME`,
     `AZURE_AI_PROJECT_ID`, `AZURE_AI_PROJECT_NAME`,
     `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `FOUNDRY_PROJECT_ENDPOINT`,
     `AZURE_COSMOS_ACCOUNT_NAME`, `AZURE_COSMOS_ENDPOINT`,
     `AZURE_COSMOS_DATABASE_NAME`, `AZURE_COSMOS_CONTAINER_NAME`,
     `AZURE_SEARCH_SERVICE_NAME`, `AZURE_SEARCH_ENDPOINT`,
     `AZURE_SEARCH_INDEX_NAME`, `AZURE_STORAGE_ACCOUNT_NAME`,
     `AZURE_CONTAINER_REGISTRY_NAME`, `AZURE_CONTAINER_REGISTRY_ENDPOINT`,
     `APPLICATIONINSIGHTS_CONNECTION_STRING`, `AZURE_VIRTUAL_NETWORK_NAME`,
     subnet IDs, admin VM/Bastion names, and `ENABLE_HOSTED_AGENTS=true`.

2. **`infra/modules-network-secured/ai-account-identity.bicep`** was adapted
   for the required Foundry **dual inbound path**: the account keeps
   `publicNetworkAccess: 'Enabled'` (upstream sets `'Disabled'`) with
   `networkAcls.defaultAction: 'Deny'` and exactly one narrow allowlisted IPv4 CIDR
   supplied by the new `allowedClientIpCidr` parameter. The private endpoint
   from `private-endpoint-and-dns.bicep` is unchanged and remains the primary
   path; the agent subnet's default/public outbound behavior is unchanged (no
   NAT Gateway or Firewall was added).

3. **`infra/modules-network-secured/application-insights.bicep`** gained one
   additional output, `appInsightsConnectionString`, so `main.bicep` can
   populate `APPLICATIONINSIGHTS_CONNECTION_STRING` without a second
   `existing` resource lookup. No behavior change.

4. **`infra/modules-local/workload-cosmos-database.bicep`** (new, not part of
   upstream) creates the workload-specific Cosmos DB SQL database/container —
   `agent-framework` / `chat-history`, partition key `/session_id` — on the
   Cosmos DB account that `standard-dependent-resources.bicep` provisions.
   This is separate from the `enterprise_memory` database that the project
   capability host auto-provisions for agent thread/file storage; there is no
   data migration, this is a fresh container.

5. **`infra/modules-local/admin-access.bicep`** (new, not part of upstream)
   adds private admin access: an `admin-subnet` and the fixed-name
   `AzureBastionSubnet` added to the upstream-created VNet via the same
   `Microsoft.Network/virtualNetworks/subnets` sub-resource technique
   upstream's own `subnet.bicep` / `existing-vnet.bicep` use (so it never
   races with or replaces the official agent/PE subnets), a Standard Azure
   Bastion host with a Standard static public IP, a Windows Server 2022
   (Trusted Launch, Gen2) VM with **no public IP** reachable only through
   Bastion, an admin-subnet NSG that allows RDP only from the
   `AzureBastionSubnet` range, the `AADLoginForWindows` extension for Entra ID
   sign-in, and the built-in **Virtual Machine Administrator Login** role
   assignment for the deploying `principalId`. The admin VM password is never
   emitted as a template output.

## Not changed from upstream

- Cosmos DB, Storage, and Azure AI Search keep `publicNetworkAccess:
  'Disabled'` / local-auth-disabled where upstream already disables it
  (Cosmos DB `disableLocalAuth: true`, Storage `allowSharedKeyAccess: false`).
  Azure AI Search keeps upstream's `disableLocalAuth: false` +
  `authOptions.aadOrApiKey` combination because the Standard Agent capability
  host's CognitiveSearch connection requires that exact configuration
  (documented in upstream's `validate-search-aad-auth.bicep`); disabling local
  auth further is not compatible with the capability host as shipped upstream.
- Azure Container Registry defaults to `publicNetworkAccess: 'Disabled'`.
  Supplying `developerIpCidr` enables public access only for that narrow
  allowlisted IPv4 CIDR, rather than enabling unrestricted public access; the
  parameter remains empty by default in `main.parameters.json`.
- No NAT Gateway or Azure Firewall was added to the agent subnet; its
  default/public outbound behavior is unchanged from upstream.
