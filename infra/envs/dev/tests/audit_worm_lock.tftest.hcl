mock_provider "azurerm" {
  mock_data "azurerm_cognitive_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-foundry-test/providers/Microsoft.CognitiveServices/accounts/foundry-test"
    }
  }
}
mock_provider "azapi" {}

variables {
  subscription_id               = "00000000-0000-0000-0000-000000000000"
  storage_account_name          = "dataforgeaudittest"
  search_service_name           = "dataforge-search-test"
  speech_account_name           = "dataforgespeechtest"
  acr_name                      = "dataforgeacrtest"
  backend_image                 = "dataforgeacrtest.azurecr.io/backend:test"
  mcp_image                     = "dataforgeacrtest.azurecr.io/mcp:test"
  foundry_resource_group_name   = "rg-foundry-test"
  foundry_account_name          = "foundry-test"
  audit_key_vault_id            = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-dataforge-test/providers/Microsoft.KeyVault/vaults/dataforge-test"
  audit_hmac_keyring_secret_uri = "https://dataforge-test.vault.azure.net/secrets/dataforge-audit-hmac-keys"
}

run "reject_missing_irreversible_confirmation" {
  command = plan

  variables {
    audit_immutability_locked            = true
    audit_immutability_lock_confirmation = ""
  }

  expect_failures = [
    var.audit_immutability_lock_confirmation,
  ]
}

run "accept_explicit_irreversible_confirmation" {
  command = plan

  variables {
    audit_immutability_locked            = true
    audit_immutability_lock_confirmation = "LOCK_DATAFORGE_AUDIT_WORM"
  }
}
