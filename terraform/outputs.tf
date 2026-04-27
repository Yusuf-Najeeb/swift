output "resource_group_name" {
  value       = azurerm_resource_group.swift.name
  description = "Resource group containing Swift infrastructure."
}

output "article_storage_account_name" {
  value       = azurerm_storage_account.articles.name
  description = "Storage account (Blob) for saved article .md files."
}

output "article_blob_container_name" {
  value       = var.articles_blob_container_name
  description = "Blob container name injected into the backend as AZURE_STORAGE_CONTAINER_NAME."
}

output "acr_login_server" {
  value       = azurerm_container_registry.swift.login_server
  description = "Login server for docker push, e.g. myacr.azurecr.io"
}

output "backend_url" {
  value       = local.backend_public_url
  description = "Public HTTPS URL for the FastAPI app. Use this as NEXT_PUBLIC_API_URL when building the frontend image."
}

output "frontend_url" {
  value       = length(azurerm_container_app.frontend) > 0 ? "https://${azurerm_container_app.frontend[0].ingress[0].fqdn}" : null
  description = "Public HTTPS URL for the Next.js app (null until frontend_image is set and applied)."
}

output "acr_admin_username" {
  value       = var.acr_use_admin ? azurerm_container_registry.swift.admin_username : null
  sensitive   = false
  description = "ACR admin user (only when acr_use_admin is true)."
}

output "acr_admin_password" {
  value       = var.acr_use_admin ? azurerm_container_registry.swift.admin_password : null
  sensitive   = true
  description = "ACR admin password for docker login (only when acr_use_admin is true)."
}
