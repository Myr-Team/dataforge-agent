variable "resource_group_name" { type = string }
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

resource "azurerm_container_app" "backend" {
  name                         = var.backend_app_name
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
