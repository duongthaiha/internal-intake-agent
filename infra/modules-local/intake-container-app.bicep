@description('Azure region for the Container Apps resources.')
param location string

@description('Container image deployed by azd.')
param imageName string

@description('Resource ID of the dedicated Container Apps infrastructure subnet.')
param infrastructureSubnetId string

@description('Whether the deployment uses the provisioned Azure Container Registry.')
param useContainerRegistry bool

@description('Login server of the provisioned Azure Container Registry.')
param containerRegistryLoginServer string

@description('Resource ID of the intake API user-assigned managed identity.')
param identityResourceId string

@description('Client ID of the intake API user-assigned managed identity.')
param identityClientId string

@description('Dedicated intake Cosmos DB endpoint.')
param cosmosEndpoint string

@description('Dedicated intake Cosmos DB database name.')
param cosmosDatabaseName string

@description('Dedicated intake Cosmos DB container name.')
param cosmosContainerName string

@description('Microsoft Entra tenant accepted by the intake API.')
param entraTenantId string

@description('Microsoft Entra application audience accepted by the intake API.')
param entraAudience string

@description('Application role required for privileged intake reads.')
param privilegedReadRole string

@description('Application role required for privileged intake writes.')
param privilegedWriteRole string

@description('Delegated scope required for requester intake writes.')
param delegatedWriteScope string

@description('Application Insights connection string.')
param applicationInsightsConnectionString string

@description('Application log level.')
param logLevel string

@description('Minimum number of intake API replicas.')
@minValue(0)
param minReplicas int = 1

@description('Maximum number of intake API replicas.')
@minValue(1)
param maxReplicas int = 5

var serviceName = 'intake-api'
var environmentName = 'cae-intake-${uniqueString(resourceGroup().id)}'
var serviceImageName = !empty(imageName)
  ? imageName
  : (useContainerRegistry ? '${containerRegistryLoginServer}/${serviceName}' : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest')
var deploymentImageName = !empty(imageName)
  ? imageName
  : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: environmentName
  location: location
  properties: {
    publicNetworkAccess: 'Disabled'
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      internal: false
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: serviceName
  location: location
  tags: {
    'azd-service-name': serviceName
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8000
        transport: 'Auto'
      }
      registries: useContainerRegistry ? [
        {
          server: containerRegistryLoginServer
          identity: identityResourceId
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: serviceName
          image: deploymentImageName
          env: [
            {
              name: 'INTAKE_COSMOS_ENDPOINT'
              value: cosmosEndpoint
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'INTAKE_COSMOS_DATABASE_NAME'
              value: cosmosDatabaseName
            }
            {
              name: 'INTAKE_COSMOS_CONTAINER_NAME'
              value: cosmosContainerName
            }
            {
              name: 'INTAKE_ENTRA_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'INTAKE_ENTRA_AUDIENCE'
              value: entraAudience
            }
            {
              name: 'INTAKE_PRIVILEGED_READ_ROLE'
              value: privilegedReadRole
            }
            {
              name: 'INTAKE_PRIVILEGED_WRITE_ROLE'
              value: privilegedWriteRole
            }
            {
              name: 'INTAKE_DELEGATED_WRITE_SCOPE'
              value: delegatedWriteScope
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsightsConnectionString
            }
            {
              name: 'LOG_LEVEL'
              value: logLevel
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

output environmentName string = environment.name
output environmentId string = environment.id
output environmentDefaultDomain string = environment.properties.defaultDomain
output appName string = app.name
output imageName string = serviceImageName
output uri string = 'https://${app.properties.configuration.ingress.fqdn}'
