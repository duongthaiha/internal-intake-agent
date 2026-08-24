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
   - New parameters and modules for the Cosmos DB workload database, selected
     public CIDR access, security-control tagging, and private admin access (see
     below).
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
   supplied by `allowedClientIpCidr` (default `85.210.10.0/24`). The private endpoint
   from `private-endpoint-and-dns.bicep` is unchanged and remains the primary
   path; the agent subnet's default/public outbound behavior is unchanged (no
   NAT Gateway or Firewall was added). The template-created account also carries
   `SecurityControl=Ignore`.

3. **`infra/modules-network-secured/standard-dependent-resources.bicep`** now
   gives template-created Cosmos DB, Azure AI Search, and Storage resources the
   `SecurityControl=Ignore` tag and selected-network access for
   `85.210.10.0/24`. Each private endpoint remains in place; ACLs stay
   deny-by-default and existing local/key-authentication restrictions are
   unchanged. Externally supplied resources remain unmodified.

4. **`infra/modules-network-secured/ai-project-identity.bicep`** and
   **`container-registry.bicep`** apply the same tag to the template-created
   Foundry project and ACR. ACR retains its private endpoint and uses the same
   selected-network CIDR with `defaultAction: 'Deny'`.

5. **`infra/modules-network-secured/application-insights.bicep`** gained one
   additional output, `appInsightsConnectionString`, so `main.bicep` can
   populate `APPLICATIONINSIGHTS_CONNECTION_STRING` without a second
   `existing` resource lookup. No behavior change.

6. **`infra/modules-local/workload-cosmos-database.bicep`** (new, not part of
   upstream) creates the workload-specific Cosmos DB SQL database/container —
   `agent-framework` / `chat-history`, partition key `/session_id` — on the
   Cosmos DB account that `standard-dependent-resources.bicep` provisions.
   This is separate from the `enterprise_memory` database that the project
   capability host auto-provisions for agent thread/file storage; there is no
   data migration, this is a fresh container.

7. **`infra/modules-local/intake-cosmos.bicep`** creates the dedicated intake
   Cosmos DB account with the same tag and selected-network CIDR. Its private
   endpoint, private DNS integration, TLS minimum, local-authentication posture,
   SQL database, and SQL container remain unchanged.

8. **`infra/modules-local/admin-access.bicep`** (new, not part of upstream)
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

## Preserved behavior

- The Foundry account retains key-based local authentication
  (`disableLocalAuth: false`) while its public endpoint remains restricted by
  the configured selected-network rule and private endpoint.
- Cosmos DB, Storage, and Azure AI Search keep the upstream private endpoints
  while adding one selected-network public CIDR. Cosmos DB keeps
  `disableLocalAuth: true`, Storage keeps `allowSharedKeyAccess: false`, and
  Azure AI Search keeps upstream's `disableLocalAuth: false` +
  `authOptions.aadOrApiKey` combination because the Standard Agent capability
  host's CognitiveSearch connection requires that exact configuration
  (documented in upstream's `validate-search-aad-auth.bicep`); disabling local
  auth further is not compatible with the capability host as shipped upstream.
- Azure Container Registry defaults to selected-network public access for
  `85.210.10.0/24` and retains its private endpoint. The
  `AZURE_ACR_DEVELOPER_IP_CIDR` environment contract remains available for an
  explicitly approved override; unrestricted public access is never enabled.
- No NAT Gateway or Azure Firewall was added to the agent subnet; its
  default/public outbound behavior is unchanged from upstream.
