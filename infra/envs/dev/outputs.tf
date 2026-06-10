output "resource_group_name" {
  value = module.resource_group.name
}

output "storage_account_name" {
  value = module.storage.storage_account_name
}

output "storage_blob_endpoint" {
  value = module.storage.primary_blob_endpoint
}

output "search_endpoint" {
  value = module.search.search_endpoint
}

output "search_primary_key" {
  value     = module.search.search_primary_key
  sensitive = true
}

output "speech_endpoint" {
  value = module.speech.speech_endpoint
}

output "speech_region" {
  value = var.region
}

output "speech_primary_key" {
  value     = module.speech.primary_access_key
  sensitive = true
}

output "acr_login_server" {
  value = module.acr.login_server
}

output "backend_container_app_default_hostname" {
  value = module.container_apps.backend_default_hostname
}

output "mcp_container_app_default_hostname" {
  value = module.container_apps.mcp_default_hostname
}

output "app_insights_connection_string" {
  value     = module.monitoring.app_insights_connection_string
  sensitive = true
}

output "foundry_endpoint" {
  value = var.foundry_endpoint
}

output "openai_endpoint" {
  value = var.openai_endpoint
}

output "chat_deployment" {
  value = var.chat_deployment
}

output "embedding_deployment" {
  value = var.embedding_deployment
}

output "image_deployment" {
  value = var.image_deployment
}
