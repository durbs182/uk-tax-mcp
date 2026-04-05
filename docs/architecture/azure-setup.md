# Azure Resource Setup Guide

This document covers the one-time Azure provisioning steps needed to run the
HMRC content indexing pipeline and the vector search query path.

Authentication uses **Managed Identity and OIDC throughout** — no long-lived
API keys are stored in GitHub secrets or application configuration.

---

## Prerequisites

```bash
# Install Azure CLI
brew install azure-cli        # macOS
az login
az account set --subscription "<your-subscription-id>"
```

---

## 1. Resource Group

```bash
az group create \
  --name hmrc-tax-mcp-rg \
  --location uksouth
```

---

## 2. Azure OpenAI

### Create the service

```bash
az cognitiveservices account create \
  --name hmrc-tax-openai \
  --resource-group hmrc-tax-mcp-rg \
  --location uksouth \
  --kind OpenAI \
  --sku S0 \
  --yes
```

### Deploy models

```bash
# Embedding model — used by the indexing pipeline and at query time
# text-embedding-3-large is required: the Cosmos container is created with 3072 dimensions
az cognitiveservices account deployment create \
  --name hmrc-tax-openai \
  --resource-group hmrc-tax-mcp-rg \
  --deployment-name text-embedding-3-large \
  --model-name text-embedding-3-large \
  --model-version "1" \
  --model-format OpenAI \
  --sku-capacity 50 \
  --sku-name Standard

# Chat model — used by the explain / RAG endpoint
az cognitiveservices account deployment create \
  --name hmrc-tax-openai \
  --resource-group hmrc-tax-mcp-rg \
  --deployment-name gpt-4o-mini \
  --model-name gpt-4o-mini \
  --model-version "2024-07-18" \
  --model-format OpenAI \
  --sku-capacity 30 \
  --sku-name Standard
```

### Get the endpoint

```bash
az cognitiveservices account show \
  --name hmrc-tax-openai \
  --resource-group hmrc-tax-mcp-rg \
  --query properties.endpoint -o tsv
# → https://hmrc-tax-openai.openai.azure.com/
```

Set this as a GitHub Actions **variable** (not secret — it is not sensitive):
`AZURE_OPENAI_ENDPOINT`

---

## 3. Cosmos DB for NoSQL

### Create the account

```bash
az cosmosdb create \
  --name hmrc-tax-cosmos \
  --resource-group hmrc-tax-mcp-rg \
  --locations regionName=uksouth failoverPriority=0 isZoneRedundant=false \
  --default-consistency-level Session \
  --capabilities EnableNoSQLVectorSearch
```

> The `EnableNoSQLVectorSearch` capability enables vector indexing on
> containers.  It can only be added at account creation — not retroactively.

### Get the endpoint

```bash
az cosmosdb show \
  --name hmrc-tax-cosmos \
  --resource-group hmrc-tax-mcp-rg \
  --query documentEndpoint -o tsv
# → https://hmrc-tax-cosmos.documents.azure.com:443/
```

Set as GitHub Actions variable: `COSMOS_URL`
Set as GitHub Actions variable: `COSMOS_DB_NAME` = `hmrc-guidance`
Set as GitHub Actions variable: `COSMOS_CONTAINER` = `hmrc-chunks`

### Create the database and container

The `upload_to_cosmos.py` script calls `create_database_if_not_exists` and
`create_container_if_not_exists` automatically on first run, applying the
DiskANN vector policy.  No manual step is needed beyond account creation.

If you need to create the container manually (e.g. for testing):

```bash
# Database
az cosmosdb sql database create \
  --account-name hmrc-tax-cosmos \
  --resource-group hmrc-tax-mcp-rg \
  --name hmrc-guidance

# Container — vector policy must be set at creation via the portal or SDK;
# the CLI does not yet expose vector-specific options directly.
# Use the Python script: python scripts/indexing/upload_to_cosmos.py --dry-run
```

---

## 4. Managed Identity for GitHub Actions (OIDC)

OIDC lets GitHub Actions authenticate to Azure without storing any credentials.

### Create a user-assigned managed identity

```bash
az identity create \
  --name hmrc-tax-mcp-deployer \
  --resource-group hmrc-tax-mcp-rg
```

### Get the identity details

```bash
az identity show \
  --name hmrc-tax-mcp-deployer \
  --resource-group hmrc-tax-mcp-rg \
  --query '{clientId:clientId, principalId:principalId, tenantId:tenantId}' \
  -o json
```

Add the three values as GitHub Actions **secrets**:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | `clientId` from above |
| `AZURE_TENANT_ID` | `tenantId` from above |
| `AZURE_SUBSCRIPTION_ID` | Your Azure subscription ID |

### Add federated credential (OIDC trust)

```bash
az identity federated-credential create \
  --name github-actions-indexing \
  --identity-name hmrc-tax-mcp-deployer \
  --resource-group hmrc-tax-mcp-rg \
  --issuer https://token.actions.githubusercontent.com \
  --subject repo:durbs182/hmrc-tax-mcp:ref:refs/heads/main \
  --audiences api://AzureADTokenExchange
```

For `workflow_dispatch` from any branch, add a second credential:

```bash
az identity federated-credential create \
  --name github-actions-indexing-dispatch \
  --identity-name hmrc-tax-mcp-deployer \
  --resource-group hmrc-tax-mcp-rg \
  --issuer https://token.actions.githubusercontent.com \
  --subject repo:durbs182/hmrc-tax-mcp:environment:indexing \
  --audiences api://AzureADTokenExchange
```

---

## 5. RBAC Role Assignments

Grant the managed identity the minimum permissions it needs.

### Azure OpenAI — Cognitive Services OpenAI User

```bash
OPENAI_ID=$(az cognitiveservices account show \
  --name hmrc-tax-openai \
  --resource-group hmrc-tax-mcp-rg \
  --query id -o tsv)

PRINCIPAL_ID=$(az identity show \
  --name hmrc-tax-mcp-deployer \
  --resource-group hmrc-tax-mcp-rg \
  --query principalId -o tsv)

az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Cognitive Services OpenAI User" \
  --scope "$OPENAI_ID"
```

### Cosmos DB — Cosmos DB Built-in Data Contributor

Cosmos DB uses its own RBAC system (separate from Azure RBAC):

```bash
COSMOS_ID=$(az cosmosdb show \
  --name hmrc-tax-cosmos \
  --resource-group hmrc-tax-mcp-rg \
  --query id -o tsv)

az cosmosdb sql role assignment create \
  --account-name hmrc-tax-cosmos \
  --resource-group hmrc-tax-mcp-rg \
  --role-definition-id 00000000-0000-0000-0000-000000000002 \
  --principal-id "$PRINCIPAL_ID" \
  --scope "$COSMOS_ID"
```

> Role ID `00000000-0000-0000-0000-000000000002` is the built-in
> **Cosmos DB Built-in Data Contributor** role.

---

## 6. Local Development Authentication

Developers can run the Cosmos DB indexing scripts locally without any API keys:

```bash
az login                    # authenticates DefaultAzureCredential
az account set --subscription "<your-subscription-id>"

export AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com/
export COSMOS_URL=https://<your-cosmos-account>.documents.azure.com:443/
export COSMOS_DB_NAME=hmrc-guidance
export COSMOS_CONTAINER=hmrc-chunks

# Step 1: tag rules and build citation map
python scripts/indexing/tag_rules.py

# Step 2: fetch HMRC manual sections
python scripts/indexing/fetch_hmrc_sources.py

# Step 3: chunk and embed (--chunks-only skips embedding API calls)
python scripts/indexing/chunk_and_embed.py

# Step 4: upload to Cosmos DB
python scripts/indexing/upload_to_cosmos.py
```

`DefaultAzureCredential` checks, in order:
1. Environment variables (CI/CD)
2. Workload Identity (ACA Managed Identity)
3. Azure CLI (`az login`) — used locally

---

## 7. Provisioned Resources (example shared subscription)

Replace the placeholders below with the names from your Azure environment. Keep the
same roles and scopes when applying RBAC.

| Resource | Name | Notes |
|---|---|---|
| Resource group | `<shared-resource-group>` | UK South |
| Cosmos DB | `<cosmos-account-name>` | Vector search enabled (`EnableNoSQLVectorSearch`) |
| Azure OpenAI | `<azure-openai-resource-name>` | UK South, S0 |
| OpenAI deployment | `<embedding-deployment-name>` | `text-embedding-3-large`, Standard SKU, 50K TPM |
| OpenAI deployment | `<chat-deployment-name>` | Request quota via Azure portal if required |
| Managed identity | `<user-assigned-managed-identity-name>` | User-assigned |

### OIDC federated credentials on `<user-assigned-managed-identity-name>`

| Name | Subject |
|---|---|
| `<github-actions-main-credential-name>` | `repo:<github-org>/<github-repo>:ref:refs/heads/main` |
| `<github-actions-dispatch-credential-name>` | `repo:<github-org>/<github-repo>:workflow_dispatch` |

### RBAC assignments

| Role | Scope |
|---|---|
| Cognitive Services OpenAI User | `<azure-openai-resource-name>` resource |
| Cosmos DB Built-in Data Contributor | `<cosmos-account-name>` account |

## 8. GitHub Actions Variables and Secrets

Configure the following in your repository's Settings → Actions.

### Variables (non-sensitive — visible in Settings → Variables → Actions)

| Variable | Value |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | `https://<your-openai-resource>.openai.azure.com/` |
| `COSMOS_URL` | `https://<your-cosmos-account>.documents.azure.com:443/` |
| `COSMOS_DB_NAME` | `hmrc-guidance` |
| `COSMOS_CONTAINER` | `hmrc-chunks` |

### Secrets (Settings → Secrets → Actions)

| Secret | Description |
|---|---|
| `AZURE_CLIENT_ID` | Managed identity client ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

No API keys, connection strings, or passwords are stored anywhere.

## 9. Pending: GPT-4o-mini quota

If your subscription has 0 quota for `gpt-4o-mini GlobalStandard`, this affects the RAG
explain endpoint but not the embedding/indexing pipeline.

To request quota:
1. Azure portal → **Subscriptions** → your subscription → **Usage + quotas**
2. Filter: `OpenAI`, region `UK South`
3. Request increase for `OpenAI.GlobalStandard.gpt-4o-mini` to 30K TPM

Alternatively use `gpt-4o` (Standard SKU) which has quota available — more expensive
but identical API surface.

---

## 10. Estimated Monthly Cost

| Resource | SKU | Cost |
|---|---|---|
| Azure OpenAI (embeddings, indexing) | text-embedding-3-large | ~£2–5/month |
| Azure OpenAI (chat, runtime) | GPT-4o-mini | ~£2–30/month (usage-dependent) |
| Cosmos DB | Serverless | ~£10–40/month |
| **Total (vector search via Cosmos)** | | **~£14–75/month** |

This is approximately £180–240/month cheaper than Azure AI Search Standard S1
while providing equivalent functionality for this index size (~116MB).
