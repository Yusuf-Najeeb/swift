locals {
  openrouter_secret_name   = "openrouter-api-key"
  acr_password_secret_name = "acr-password"

  backend_public_url = "https://${azurerm_container_app.backend.latest_revision_fqdn}"
}

resource "azurerm_container_app" "backend" {
  name                         = var.backend_app_name
  container_app_environment_id = azurerm_container_app_environment.swift.id
  resource_group_name          = azurerm_resource_group.swift.name
  revision_mode                = "Single"

  secret {
    name  = local.openrouter_secret_name
    value = var.openrouter_api_key
  }

  dynamic "secret" {
    for_each = var.acr_use_admin ? [1] : []
    content {
      name  = local.acr_password_secret_name
      value = azurerm_container_registry.swift.admin_password
    }
  }

  dynamic "registry" {
    for_each = var.acr_use_admin ? [1] : []
    content {
      server               = azurerm_container_registry.swift.login_server
      username             = azurerm_container_registry.swift.admin_username
      password_secret_name = local.acr_password_secret_name
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "backend"
      image  = var.backend_image
      cpu    = var.backend_cpu
      memory = var.backend_memory

      env {
        name        = "OPENROUTER_API_KEY"
        secret_name = local.openrouter_secret_name
      }

      dynamic "env" {
        for_each = var.swift_api_bearer_token != "" ? [1] : []
        content {
          name  = "SWIFT_API_BEARER_TOKEN"
          value = var.swift_api_bearer_token
        }
      }
    }
  }

  ingress {
    external_enabled           = true
    target_port                = 8000
    transport                  = "http"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  tags = var.tags
}

resource "azurerm_container_app" "frontend" {
  count = var.frontend_image != "" ? 1 : 0

  name                         = var.frontend_app_name
  container_app_environment_id = azurerm_container_app_environment.swift.id
  resource_group_name          = azurerm_resource_group.swift.name
  revision_mode                = "Single"

  dynamic "secret" {
    for_each = var.acr_use_admin ? [1] : []
    content {
      name  = local.acr_password_secret_name
      value = azurerm_container_registry.swift.admin_password
    }
  }

  dynamic "registry" {
    for_each = var.acr_use_admin ? [1] : []
    content {
      server               = azurerm_container_registry.swift.login_server
      username             = azurerm_container_registry.swift.admin_username
      password_secret_name = local.acr_password_secret_name
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "frontend"
      image  = var.frontend_image
      cpu    = var.frontend_cpu
      memory = var.frontend_memory

      env {
        name  = "NEXT_PUBLIC_API_URL"
        value = local.backend_public_url
      }

      dynamic "env" {
        for_each = var.swift_api_bearer_token != "" ? [1] : []
        content {
          name  = "SWIFT_API_BEARER_TOKEN"
          value = var.swift_api_bearer_token
        }
      }
    }
  }

  ingress {
    external_enabled           = true
    target_port                = 3000
    transport                  = "http"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  depends_on = [azurerm_container_app.backend]

  tags = var.tags
}
