param accountName string
param projectName string
param projectPrincipalId string
param evaluationOperatorPrincipalId string
param createAccountRoleAssignments bool = true

var foundryUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

resource scheduledEvaluationRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, projectPrincipalId, foundryUserRoleId)
  scope: project
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleId)
  }
}

resource scheduledEvaluationModelInferenceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createAccountRoleAssignments) {
  name: guid(account.id, projectPrincipalId, cognitiveServicesOpenAIUserRoleId)
  scope: account
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
  }
}

resource evaluationOperatorModelInferenceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createAccountRoleAssignments) {
  name: guid(account.id, evaluationOperatorPrincipalId, cognitiveServicesOpenAIUserRoleId)
  scope: account
  properties: {
    principalId: evaluationOperatorPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
  }
}

output roleAssignmentId string = scheduledEvaluationRoleAssignment.id
output modelInferenceRoleAssignmentId string = createAccountRoleAssignments ? scheduledEvaluationModelInferenceRoleAssignment!.id : ''
output operatorModelInferenceRoleAssignmentId string = createAccountRoleAssignments ? evaluationOperatorModelInferenceRoleAssignment!.id : ''
