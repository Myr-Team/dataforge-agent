variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "search_service_name" { type = string }

resource "azurerm_search_service" "this" {
  name                = var.search_service_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "standard"
  replica_count       = 1
  partition_count     = 1
}

output "search_service_id" {
  value = azurerm_search_service.this.id
}

output "search_endpoint" {
  value = "https://${azurerm_search_service.this.name}.search.windows.net"
}

output "search_primary_key" {
  value     = azurerm_search_service.this.primary_key
  sensitive = true
}

