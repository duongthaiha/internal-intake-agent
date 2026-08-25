@description('Name of the existing API Management service.')
param serviceName string

@description('Public HTTPS URL of the intake Container App.')
param intakeBackendUrl string

@description('Microsoft Entra tenant accepted by the intake API and MCP endpoint.')
param entraTenantId string

@description('Microsoft Entra application audience accepted by the intake API and MCP endpoint.')
param entraAudience string

@description('Maximum MCP tool calls allowed in each rate-limit window.')
@minValue(1)
param rateLimitCalls int = 60

@description('MCP rate-limit window in seconds.')
@minValue(1)
param rateLimitRenewalPeriod int = 60

var intakeApiId = 'intake-api'
var intakeMcpId = 'intake-mcp'
var intakeOpenApi = loadTextContent('../../openapi/intake-api.openapi.json')
var operationIds = [
  'create_intake_request'
  'get_intake_request'
  'list_intake_requests'
  'replace_intake_request'
  'submit_intake_request'
]
var toolDescriptions = [
  'Create an intake request draft.'
  'Get an authorized intake request.'
  'List intake requests visible to the caller.'
  'Replace an intake request draft.'
  'Submit an intake request draft.'
]
var mcpPolicyTemplate = '''<policies>
  <inbound>
    <base />
    <validate-azure-ad-token tenant-id="__TENANT_ID__" header-name="Authorization" failed-validation-httpcode="401" failed-validation-error-message="Unauthorized. Access token is missing or invalid.">
      <audiences>
        <audience>__AUDIENCE__</audience>
        <audience>__CLIENT_ID_AUDIENCE__</audience>
      </audiences>
    </validate-azure-ad-token>
    <rate-limit-by-key calls="__RATE_CALLS__" renewal-period="__RATE_PERIOD__" counter-key="@(context.Request.Headers.GetValueOrDefault(&quot;Authorization&quot;,&quot;anonymous&quot;).GetHashCode().ToString())" />
    <set-header name="Authorization" exists-action="override">
      <value>@(context.Request.Headers.GetValueOrDefault("Authorization"))</value>
    </set-header>
  </inbound>
  <backend>
    <forward-request />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>'''
var mcpPolicyWithTenant = replace(mcpPolicyTemplate, '__TENANT_ID__', entraTenantId)
var mcpPolicyWithAudience = replace(mcpPolicyWithTenant, '__AUDIENCE__', entraAudience)
var entraClientIdAudience = startsWith(toLower(entraAudience), 'api://')
  ? substring(entraAudience, 6)
  : entraAudience
var mcpPolicyWithClientIdAudience = replace(
  mcpPolicyWithAudience,
  '__CLIENT_ID_AUDIENCE__',
  entraClientIdAudience
)
var mcpPolicyWithRateCalls = replace(mcpPolicyWithClientIdAudience, '__RATE_CALLS__', string(rateLimitCalls))
var mcpPolicy = replace(mcpPolicyWithRateCalls, '__RATE_PERIOD__', string(rateLimitRenewalPeriod))

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: serviceName
}

resource intakeApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: intakeApiId
  properties: {
    displayName: 'Intake Request API'
    description: 'Private intake REST API backing the APIM MCP projection.'
    path: 'intake'
    protocols: [
      'https'
    ]
    serviceUrl: intakeBackendUrl
    subscriptionRequired: false
    type: 'http'
    format: 'openapi+json'
    value: intakeOpenApi
  }
}

// APIM MCP resources require API version 2025-09-01-preview.
resource intakeMcp 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: intakeMcpId
  properties: {
    displayName: 'Intake MCP Server'
    description: 'Streamable HTTP MCP tools backed by the private intake API.'
    path: intakeMcpId
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    type: 'mcp'
  }
  dependsOn: [
    intakeApi
  ]
}

@batchSize(1)
resource intakeTools 'Microsoft.ApiManagement/service/apis/tools@2025-09-01-preview' = [
  for (operationId, index) in operationIds: {
    parent: intakeMcp
    name: operationId
    properties: {
      displayName: operationId
      description: toolDescriptions[index]
      operationId: resourceId(
        'Microsoft.ApiManagement/service/apis/operations',
        serviceName,
        intakeApiId,
        operationId
      )
    }
  }
]

resource intakeMcpPolicy 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = {
  parent: intakeMcp
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: mcpPolicy
  }
}

output serviceName string = apim.name
output gatewayUrl string = 'https://${apim.name}.azure-api.net'
output restApiUrl string = 'https://${apim.name}.azure-api.net/intake'
output mcpServerUrl string = 'https://${apim.name}.azure-api.net/${intakeMcpId}/mcp'
