# Swift Writer — Azure (Terraform)

Creates a **resource group**, **Azure Container Registry**, **Log Analytics**, **Container Apps Environment**, and:

1. **Backend** Container App (FastAPI on port 8000)
2. **Frontend** Container App (Next.js on port 3000) — only if `frontend_image` is non-empty (two-step deploy; see below)

## What you need before running Terraform

| Item | Why |
|------|-----|
| **Azure subscription** | Billing + quota for Container Apps and ACR |
| **Rights on the subscription** (or an existing RG) | At least **Contributor** on the subscription (this module creates a new RG) |
| **Azure CLI** + login | `az login` and `az account set --subscription <id>` if you have several |
| **Terraform ≥ 1.5** | [Install Terraform](https://developer.hashicorp.com/terraform/install) |
| **Chosen Azure region** | e.g. `eastus` — set `location` |
| **Globally unique ACR name** | Letters and numbers only, 5–50 chars (`acr_name`) |
| **Names for RG, Container Apps Environment, and both apps** | Must follow [ACA naming rules](https://learn.microsoft.com/azure/container-apps/overview) (app names: lowercase, alphanumeric, hyphens; max length applies) |
| **OpenRouter API key** | Pass as `TF_VAR_openrouter_api_key` or in `terraform.tfvars` (sensitive) |
| **Docker images in ACR** | Build and `docker push` **backend** first; see workflow below |
| **(Optional) `swift_api_bearer_token`** | Same semantics as app `SWIFT_API_BEARER_TOKEN` if you lock the API |

Terraform **does not** build images. You build locally or in CI, push to ACR, then point `backend_image` / `frontend_image` at those tags.

## Quick start

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars (and/or export TF_VAR_* for secrets)

terraform init
terraform plan
terraform apply
```

## Two-step image workflow (recommended)

The frontend image must be built with `NEXT_PUBLIC_API_URL` equal to the **public backend URL** (HTTPS). That URL exists only after the backend app exists.

1. **First apply** — set `frontend_image = ""` in `terraform.tfvars`. From the **repo root**, build and push the **backend** image (defaults to **including** Node/npm for `npx` / Serper; add `--build-arg WITH_NODE=0` for a slimmer image):
   ```bash
   docker build -f backend/Dockerfile -t "<acr_login_server>/swift-backend:1.0.0" .
   az acr login --name "<acr_name_only>"
   docker push "<acr_login_server>/swift-backend:1.0.0"
   ```
   Set `backend_image` in `terraform.tfvars` to that reference, then run `terraform apply`.
2. **Get backend URL** — `terraform output -raw backend_url`
3. **Build frontend** — from repo root, with the URL from step 2:
   ```bash
   docker build -f frontend/Dockerfile frontend \
     --build-arg "NEXT_PUBLIC_API_URL=$(terraform output -raw backend_url)" \
     -t "$(terraform output -raw acr_login_server)/swift-frontend:1.0.0"
   az acr login --name "$(terraform output -raw acr_login_server | cut -d. -f1)"
   docker push "$(terraform output -raw acr_login_server)/swift-frontend:1.0.0"
   ```
4. **Second apply** — set `frontend_image` in `terraform.tfvars` to that full image reference, then `terraform apply` again.

## ACR authentication

With `acr_use_admin = true` (default), Terraform uses the ACR **admin account** for pull secrets (simple for first deploy). For stricter production, set `acr_use_admin = false` and extend the module to use a **managed identity** + **AcrPull** role (not included in this minimal stack).

## Secrets in state

Sensitive values (API keys, ACR admin password) are still recorded in Terraform state. Use a **remote backend** with encryption (e.g. Azure Storage + `azurerm` backend) and lock down access. For OpenRouter, prefer `export TF_VAR_openrouter_api_key=...` over committing `terraform.tfvars`.

## Destroy

```bash
terraform destroy
```

## Optional: subscription ID

If `az account show` is not the subscription you want, set it with `az account set --subscription <id>` before `terraform plan`, or add `subscription_id` to the `azurerm` provider in `providers.tf` (not wired by default).
