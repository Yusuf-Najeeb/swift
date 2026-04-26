resource "azurerm_resource_group" "swift" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_log_analytics_workspace" "swift" {
  name                = var.log_analytics_workspace_name
  location            = azurerm_resource_group.swift.location
  resource_group_name = azurerm_resource_group.swift.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "swift" {
  name                       = var.container_app_environment_name
  location                   = azurerm_resource_group.swift.location
  resource_group_name        = azurerm_resource_group.swift.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.swift.id
  tags                       = var.tags
}

resource "azurerm_container_registry" "swift" {
  name                = var.acr_name
  resource_group_name = azurerm_resource_group.swift.name
  location            = azurerm_resource_group.swift.location
  sku                 = "Basic"
  admin_enabled       = var.acr_use_admin
  tags                = var.tags
}
