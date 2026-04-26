variable "location" {
  type        = string
  description = "Azure region, e.g. eastus, westeurope."
}

variable "log_analytics_workspace_name" {
  type        = string
  description = "Log Analytics workspace name (max 63 chars; letters, numbers, hyphen)."
  default     = "swift-writer-logs"
}

variable "resource_group_name" {
  type        = string
  description = "Resource group that will be created to hold all Swift resources."
}

variable "acr_name" {
  type        = string
  description = "Globally unique ACR name (letters and numbers only, 5–50 chars). Example: swiftwriteracr2026"

  validation {
    condition     = can(regex("^[a-zA-Z0-9]{5,50}$", var.acr_name))
    error_message = "acr_name must be 5–50 alphanumeric characters (no hyphens)."
  }
}

variable "acr_use_admin" {
  type        = bool
  description = "Enable ACR admin user for container pull secrets. Easier for first deploy; prefer managed identity + AcrPull later."
  default     = true
}

variable "container_app_environment_name" {
  type        = string
  description = "Name for the Container Apps environment (unique within RG)."
}

variable "backend_app_name" {
  type        = string
  description = "Container App name for the FastAPI backend (DNS label rules: lowercase alphanumeric and hyphens)."
}

variable "frontend_app_name" {
  type        = string
  description = "Container App name for the Next.js frontend."
}

variable "backend_image" {
  type        = string
  description = "Full container image for backend, e.g. myregistry.azurecr.io/swift-backend:1.0.0 (must exist before apply)."
}

variable "frontend_image" {
  type        = string
  description = "Full container image for frontend. Build with NEXT_PUBLIC_API_URL pointing at backend HTTPS URL (see README)."
}

variable "openrouter_api_key" {
  type        = string
  description = "OpenRouter API key (stored as an ACA secret, not in state as plain text if you use TF_VAR_)."
  sensitive   = true
}

variable "swift_api_bearer_token" {
  type        = string
  description = "Optional. Same as SWIFT_API_BEARER_TOKEN; if empty string, auth is disabled on the API."
  default     = ""
  sensitive   = true
}

variable "backend_cpu" {
  type        = number
  description = "Backend container vCPU (e.g. 0.5, 1.0)."
  default     = 0.5
}

variable "backend_memory" {
  type        = string
  description = "Backend container memory, e.g. 1Gi."
  default     = "1Gi"
}

variable "frontend_cpu" {
  type    = number
  default = 0.5
}

variable "frontend_memory" {
  type    = string
  default = "1Gi"
}

variable "min_replicas" {
  type        = number
  description = "Minimum replicas per app (0 allows scale-to-zero; first request may be slow)."
  default     = 0
}

variable "max_replicas" {
  type    = number
  default = 3
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the resource group and major resources."
  default     = {}
}
