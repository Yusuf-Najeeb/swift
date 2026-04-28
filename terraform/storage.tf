resource "azurerm_storage_account" "articles" {
  name                = var.storage_account_name
  resource_group_name = azurerm_resource_group.swift.name
  location            = azurerm_resource_group.swift.location

  account_tier                      = "Standard"
  account_replication_type          = var.storage_replication_type
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  public_network_access_enabled   = true

  tags = var.tags
}

resource "azurerm_storage_container" "article_blobs" {
  name                  = var.articles_blob_container_name
  storage_account_id   = azurerm_storage_account.articles.id
  container_access_type = "private"
}
