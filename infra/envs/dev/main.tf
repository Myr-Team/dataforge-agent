module "resource_group" {
  source   = "../../modules/resource_group"
  name     = var.resource_group_name
  location = var.region
}

module "monitoring" {
  source              = "../../modules/monitoring"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
  log_analytics_name  = var.log_analytics_name
  app_insights_name   = var.app_insights_name
}

module "storage" {
  source                    = "../../modules/storage"
  resource_group_name       = module.resource_group.name
  location                  = module.resource_group.location
  storage_account_name      = var.storage_account_name
  audit_immutability_locked = var.audit_immutability_locked
}

module "search" {
  source              = "../../modules/search"
  resource_group_name = module.resource_group.name
  location            = var.search_region
  search_service_name = var.search_service_name
}

module "speech" {
  source              = "../../modules/speech"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
  speech_account_name = var.speech_account_name
}

module "acr" {
  source              = "../../modules/acr"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
  acr_name            = var.acr_name
}

module "container_apps" {
  source                           = "../../modules/container_app"
  resource_group_name              = module.resource_group.name
  location                         = module.resource_group.location
  container_env_name               = var.container_env_name
  log_analytics_workspace_id       = module.monitoring.log_analytics_workspace_resource_id
  log_analytics_primary_shared_key = module.monitoring.log_analytics_primary_shared_key
  acr_login_server                 = module.acr.login_server
  backend_app_name                 = var.backend_app_name
  backend_image                    = var.backend_image
  mcp_app_name                     = var.mcp_app_name
  mcp_image                        = var.mcp_image
  app_insights_connection_string   = module.monitoring.app_insights_connection_string
  search_endpoint                  = module.search.search_endpoint
  search_index_name                = "dataforge-workspaces"
  search_key                       = var.search_key
  storage_account_name             = module.storage.storage_account_name
  storage_blob_endpoint            = module.storage.primary_blob_endpoint
  storage_account_resource_id      = module.storage.storage_account_id
  audit_container_name             = module.storage.audit_container_name
  audit_hmac_active_key_id         = var.audit_hmac_active_key_id
  audit_key_vault_id               = var.audit_key_vault_id
  audit_hmac_keyring_secret_uri    = var.audit_hmac_keyring_secret_uri
  speech_endpoint                  = module.speech.speech_endpoint
  speech_region                    = var.region
  speech_key                       = var.speech_key
  foundry_endpoint                 = var.foundry_endpoint
  openai_endpoint                  = var.openai_endpoint
  chat_deployment                  = var.chat_deployment
  embedding_deployment             = var.embedding_deployment
  image_deployment                 = var.image_deployment
}

data "azurerm_cognitive_account" "foundry" {
  name                = var.foundry_account_name
  resource_group_name = var.foundry_resource_group_name
}

resource "azurerm_role_assignment" "backend_storage_blob" {
  scope                = module.storage.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = module.container_apps.backend_principal_id
}

resource "azurerm_role_assignment" "backend_storage_contract_reader" {
  scope                = module.storage.storage_account_id
  role_definition_name = "Reader"
  principal_id         = module.container_apps.backend_principal_id
}

resource "azurerm_role_assignment" "mcp_storage_blob" {
  scope                = module.storage.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = module.container_apps.mcp_principal_id
}

resource "azurerm_role_assignment" "backend_search_reader" {
  scope                = module.search.search_service_id
  role_definition_name = "Search Index Data Reader"
  principal_id         = module.container_apps.backend_principal_id
}

resource "azurerm_role_assignment" "backend_speech_user" {
  scope                = module.speech.speech_account_id
  role_definition_name = "Cognitive Services Speech User"
  principal_id         = module.container_apps.backend_principal_id
}

resource "azurerm_role_assignment" "backend_openai_user" {
  scope                = data.azurerm_cognitive_account.foundry.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = module.container_apps.backend_principal_id
}

resource "azurerm_role_assignment" "backend_acr_pull" {
  scope                = module.acr.id
  role_definition_name = "AcrPull"
  principal_id         = module.container_apps.backend_principal_id
}

resource "azurerm_role_assignment" "mcp_acr_pull" {
  scope                = module.acr.id
  role_definition_name = "AcrPull"
  principal_id         = module.container_apps.mcp_principal_id
}
