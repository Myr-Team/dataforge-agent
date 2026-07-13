# ---------------------------------------------------------------------------
# Deployment-specific values. Supply these via a local terraform.tfvars file
# (git-ignored) or -var flags. See terraform.tfvars.example for the shape.
# Defaults are intentionally blank so this repo carries no account identifiers.
# ---------------------------------------------------------------------------

variable "subscription_id" {
  type        = string
  description = "Azure subscription id. Provide via tfvars; never commit it."
  default     = ""
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
  type        = string
  description = "Globally-unique storage account name (lowercase, 3-24 chars)."
  default     = ""
}

variable "audit_immutability_locked" {
  type        = bool
  description = "Irreversibly lock the dedicated audit container WORM policy. Must be true for Container Apps to accept mutations."
  default     = false
}

variable "audit_hmac_active_key_id" {
  type        = string
  description = "Non-secret ID of the active audit HMAC key. Must match a key in the Key Vault JSON key ring."
  default     = "v1"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", var.audit_hmac_active_key_id))
    error_message = "audit_hmac_active_key_id must be a valid retained key-ring identifier."
  }
}

variable "audit_key_vault_id" {
  type        = string
  description = "Resource ID of the RBAC-enabled Key Vault that stores the audit HMAC key ring."

  validation {
    condition     = can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.KeyVault/vaults/[^/]+$", var.audit_key_vault_id))
    error_message = "audit_key_vault_id must be a complete Azure Key Vault resource ID."
  }
}

variable "audit_hmac_keyring_secret_uri" {
  type        = string
  description = "Versionless Key Vault secret URI containing the audit HMAC JSON key ring."
  sensitive   = true

  validation {
    condition     = can(regex("^https://[A-Za-z0-9-]+\\.vault\\.azure\\.net/secrets/[^/]+$", var.audit_hmac_keyring_secret_uri))
    error_message = "audit_hmac_keyring_secret_uri must be a versionless Key Vault secret URI."
  }
}

variable "search_service_name" {
  type    = string
  default = ""
}

variable "speech_account_name" {
  type    = string
  default = ""
}

variable "acr_name" {
  type        = string
  description = "Globally-unique Azure Container Registry name (alphanumeric)."
  default     = ""
}

variable "backend_app_name" {
  type    = string
  default = "ca-dataforge-backend"
}

variable "backend_image" {
  type        = string
  description = "Backend container image, e.g. <acr>.azurecr.io/dataforge-backend:<tag>."
  default     = ""
}

variable "mcp_app_name" {
  type    = string
  default = "ca-dataforge-mcp"
}

variable "mcp_image" {
  type        = string
  description = "MCP container image, e.g. <acr>.azurecr.io/dataforge-mcp:<tag>."
  default     = ""
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
  type        = string
  description = "Resource group of an existing Azure AI Foundry account to reuse."
  default     = ""
}

variable "foundry_account_name" {
  type        = string
  description = "Existing Azure AI Foundry account name to reuse."
  default     = ""
}

variable "foundry_endpoint" {
  type        = string
  description = "Foundry project endpoint, e.g. https://<account>.services.ai.azure.com/api/projects/<project>."
  default     = ""
}

variable "openai_endpoint" {
  type        = string
  description = "Azure OpenAI endpoint, e.g. https://<account>.openai.azure.com/."
  default     = ""
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

variable "speech_key" {
  type        = string
  description = "Speech key injected at apply time; do not commit tfvars."
  default     = ""
  sensitive   = true
}

variable "search_key" {
  type        = string
  description = "Search admin/query key injected at apply time; do not commit tfvars."
  default     = ""
  sensitive   = true
}
