provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
  # Uses `az login` default subscription unless you set ARM_SUBSCRIPTION_ID
  # or pass -var="subscription_id=..." with subscription_id in provider (see README).
}
