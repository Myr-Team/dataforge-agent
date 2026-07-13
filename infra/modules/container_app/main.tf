variable "resource_group_name" { type = string }
variable "subscription_id" { type = string }
variable "location" { type = string }
variable "container_env_name" { type = string }
variable "log_analytics_workspace_id" { type = string }
variable "log_analytics_primary_shared_key" {
  type      = string
  sensitive = true
}
variable "acr_login_server" { type = string }
variable "backend_app_name" { type = string }
variable "backend_image" { type = string }
variable "mcp_app_name" { type = string }
variable "mcp_image" { type = string }
variable "app_insights_connection_string" {
  type      = string
  sensitive = true
}
variable "search_endpoint" { type = string }
variable "search_index_name" { type = string }
variable "search_key" {
  type      = string
  sensitive = true
  default   = ""
}
variable "storage_account_name" { type = string }
variable "storage_blob_endpoint" { type = string }
variable "storage_account_resource_id" { type = string }
variable "audit_container_name" { type = string }
variable "audit_hmac_active_key_id" {
  type = string
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", var.audit_hmac_active_key_id))
    error_message = "audit_hmac_active_key_id must be a valid retained key-ring identifier."
  }
}
variable "audit_key_vault_id" {
  type = string
  validation {
    condition     = can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.KeyVault/vaults/[^/]+$", var.audit_key_vault_id))
    error_message = "audit_key_vault_id must be a complete Azure Key Vault resource ID."
  }
}
variable "audit_hmac_keyring_secret_uri" {
  type      = string
  sensitive = true
  validation {
    condition     = can(regex("^https://[A-Za-z0-9-]+\\.vault\\.azure\\.net/secrets/[^/]+$", var.audit_hmac_keyring_secret_uri))
    error_message = "audit_hmac_keyring_secret_uri must be a versionless Key Vault secret URI."
  }
}
variable "speech_endpoint" { type = string }
variable "speech_region" { type = string }
variable "speech_key" {
  type      = string
  sensitive = true
  default   = ""
}
variable "foundry_endpoint" { type = string }
variable "openai_endpoint" { type = string }
variable "chat_deployment" { type = string }
variable "embedding_deployment" { type = string }
variable "image_deployment" { type = string }

resource "azurerm_container_app_environment" "this" {
  name                       = var.container_env_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  log_analytics_workspace_id = var.log_analytics_workspace_id
}

resource "azurerm_user_assigned_identity" "audit_secrets" {
  name                = "${var.backend_app_name}-audit-secrets"
  location            = var.location
  resource_group_name = var.resource_group_name
}

resource "azurerm_role_assignment" "audit_secrets_user" {
  scope                = var.audit_key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.audit_secrets.principal_id
}

resource "azurerm_container_app" "backend" {
  name                         = var.backend_app_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  depends_on = [azurerm_role_assignment.audit_secrets_user]

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.audit_secrets.id]
  }

  registry {
    server   = var.acr_login_server
    identity = "system"
  }

  dynamic "secret" {
    for_each = merge(
      var.speech_key == "" ? {} : { speech-key = var.speech_key },
      var.search_key == "" ? {} : { search-key = var.search_key }
    )
    content {
      name  = secret.key
      value = secret.value
    }
  }

  secret {
    name                = "audit-hmac-keyring"
    key_vault_secret_id = var.audit_hmac_keyring_secret_uri
    identity            = azurerm_user_assigned_identity.audit_secrets.id
  }

  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    container {
      name   = "backend"
      image  = var.backend_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.app_insights_connection_string
      }
      env {
        name  = "SEARCH_ENDPOINT"
        value = var.search_endpoint
      }
      env {
        name  = "SEARCH_INDEX_NAME"
        value = var.search_index_name
      }
      dynamic "env" {
        for_each = var.search_key == "" ? [] : ["search-key"]
        content {
          name        = "SEARCH_KEY"
          secret_name = env.value
        }
      }
      env {
        name  = "STORAGE_ACCOUNT_NAME"
        value = var.storage_account_name
      }
      env {
        name  = "STORAGE_BLOB_ENDPOINT"
        value = var.storage_blob_endpoint
      }
      env {
        name  = "DF_ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "DF_AUDIT_CONTAINER"
        value = var.audit_container_name
      }
      env {
        name  = "DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID"
        value = var.storage_account_resource_id
      }
      env {
        name  = "DF_AUDIT_STORAGE_SUBSCRIPTION_ID"
        value = var.subscription_id
      }
      env {
        name  = "DF_AUDIT_STORAGE_RESOURCE_GROUP"
        value = var.resource_group_name
      }
      env {
        name  = "DF_AUDIT_HMAC_ACTIVE_KEY_ID"
        value = var.audit_hmac_active_key_id
      }
      env {
        name        = "DF_AUDIT_HMAC_KEYS"
        secret_name = "audit-hmac-keyring"
      }
      env {
        name  = "SPEECH_ENDPOINT"
        value = var.speech_endpoint
      }
      env {
        name  = "SPEECH_REGION"
        value = var.speech_region
      }
      dynamic "env" {
        for_each = var.speech_key == "" ? [] : ["speech-key"]
        content {
          name        = "SPEECH_KEY"
          secret_name = env.value
        }
      }
      env {
        name  = "FOUNDRY_PROJECT_ENDPOINT"
        value = var.foundry_endpoint
      }
      env {
        name  = "OPENAI_ENDPOINT"
        value = var.openai_endpoint
      }
      env {
        name  = "DF_CHAT_DEPLOYMENT"
        value = var.chat_deployment
      }
      env {
        name  = "DF_EMBEDDING_DEPLOYMENT"
        value = var.embedding_deployment
      }
      env {
        name  = "DF_IMAGE_DEPLOYMENT"
        value = var.image_deployment
      }
    }
  }
}

resource "azurerm_container_app" "mcp" {
  name                         = var.mcp_app_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  identity {
    type = "SystemAssigned"
  }

  registry {
    server   = var.acr_login_server
    identity = "system"
  }

  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    container {
      name   = "mcp"
      image  = var.mcp_image
      cpu    = 0.25
      memory = "0.5Gi"
    }
  }
}

output "backend_principal_id" {
  value = azurerm_container_app.backend.identity[0].principal_id
}

output "mcp_principal_id" {
  value = azurerm_container_app.mcp.identity[0].principal_id
}

output "backend_default_hostname" {
  value = azurerm_container_app.backend.ingress[0].fqdn
}

output "mcp_default_hostname" {
  value = azurerm_container_app.mcp.ingress[0].fqdn
}
