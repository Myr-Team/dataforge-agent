variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "storage_account_name" { type = string }
variable "audit_immutability_locked" {
  type        = bool
  description = "Locks the audit WORM policy. Irreversible; production must set true."
  default     = false
}

resource "azurerm_storage_account" "this" {
  name                            = var.storage_account_name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_container" "workspaces" {
  name                  = "workspaces"
  storage_account_name  = azurerm_storage_account.this.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "artifacts" {
  name                  = "artifacts"
  storage_account_name  = azurerm_storage_account.this.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "audit" {
  name                  = "dataforge-audit"
  storage_account_name  = azurerm_storage_account.this.name
  container_access_type = "private"
}

resource "azurerm_storage_container_immutability_policy" "audit" {
  storage_container_resource_manager_id = azurerm_storage_container.audit.id
  immutability_period_in_days           = 365
  protected_append_writes_enabled       = true
  locked                                = var.audit_immutability_locked
}

output "storage_account_id" {
  value = azurerm_storage_account.this.id
}

output "storage_account_name" {
  value = azurerm_storage_account.this.name
}

output "primary_blob_endpoint" {
  value = azurerm_storage_account.this.primary_blob_endpoint
}

output "audit_container_name" {
  value = azurerm_storage_container.audit.name
}
