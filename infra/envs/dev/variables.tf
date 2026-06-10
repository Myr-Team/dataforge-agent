variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
  default     = "b2cb3c3d-c9f0-4dfa-bcdd-718a6a8abf10"
}

variable "region" {
  type        = string
  description = "Azure region for application resources."
  default     = "eastus2"
}

variable "search_region" {
  type        = string
  description = "Azure AI Search region. Defaults to eastus after eastus2 capacity exhaustion."
  default     = "eastus"
}

variable "resource_group_name" {
  type    = string
  default = "rg-dataforge-dev"
}

variable "storage_account_name" {
  type    = string
  default = "stdataforgedev"
}

variable "search_service_name" {
  type    = string
  default = "srch-dataforge-dev"
}

variable "speech_account_name" {
  type    = string
  default = "speech-dataforge-dev"
}

variable "acr_name" {
  type    = string
  default = "acrdataforgedev"
}

variable "backend_app_name" {
  type    = string
  default = "ca-dataforge-backend"
}

variable "mcp_app_name" {
  type    = string
  default = "ca-dataforge-mcp"
}

variable "log_analytics_name" {
  type    = string
  default = "log-dataforge-dev"
}

variable "app_insights_name" {
  type    = string
  default = "appi-dataforge-dev"
}

variable "container_env_name" {
  type    = string
  default = "cae-dataforge-dev"
}

variable "foundry_resource_group_name" {
  type    = string
  default = "Agent-Demo-Fuzh"
}

variable "foundry_account_name" {
  type    = string
  default = "Agent-Demo-Foundry-fuzh"
}

variable "foundry_endpoint" {
  type    = string
  default = "https://agent-demo-foundry-fuzh.services.ai.azure.com/api/projects/Agent-Demo-proj"
}

variable "openai_endpoint" {
  type    = string
  default = "https://agent-demo-foundry-fuzh.openai.azure.com/"
}

variable "chat_deployment" {
  type    = string
  default = "gpt-5.1"
}

variable "embedding_deployment" {
  type    = string
  default = "text-embedding-3-small"
}

variable "image_deployment" {
  type    = string
  default = "gpt-image-2"
}
