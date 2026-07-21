targetScope = 'resourceGroup'

@minLength(1)
@maxLength(50)
param serviceName string

@minLength(3)
param publisherEmail string

@minLength(1)
@maxLength(100)
param publisherName string

param location string = resourceGroup().location

resource gateway 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: serviceName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'StandardV2'
    capacity: 1
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
    publicNetworkAccess: 'Enabled'
  }
  tags: {
    application: 'dataforge'
    purpose: 'ai-gateway-monitoring'
    environment: 'preview'
    managedBy: 'bicep'
  }
}

output resourceId string = gateway.id
output gatewayUrl string = 'https://${gateway.name}.azure-api.net'
output principalId string = gateway.identity.principalId
