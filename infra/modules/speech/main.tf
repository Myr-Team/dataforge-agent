variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "speech_account_name" { type = string }

resource "azurerm_cognitive_account" "this" {
  name                = var.speech_account_name
  location            = var.location
  resource_group_name = var.resource_group_name
  kind                = "SpeechServices"
  sku_name            = "S0"
}

output "speech_account_id" {
  value = azurerm_cognitive_account.this.id
}

output "speech_endpoint" {
  value = azurerm_cognitive_account.this.endpoint
}

output "primary_access_key" {
  value     = azurerm_cognitive_account.this.primary_access_key
  sensitive = true
}
