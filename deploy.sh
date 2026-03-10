#!/usr/bin/env bash
set -euo pipefail

load_env_file() {
  local env_file="$1"
  if [[ ! -f "$env_file" ]]; then
    return
  fi
  echo "Loading environment variables from $env_file"
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

load_env_file ".env"
load_env_file ".env.deploy"

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-dealsignal-prod}"
LOCATION="${LOCATION:-eastus}"
ACR_NAME="${ACR_NAME:-dealsignalacr12345}"
ENV_NAME="${ENV_NAME:-dealsignal-env}"
JOB_NAME="${JOB_NAME:-dealsignal-nightly}"
IMAGE_NAME="${IMAGE_NAME:-dealsignal-pipeline:latest}"

IDENTITY_NAME="${IDENTITY_NAME:-dealsignal-job-identity}"
CONTAINER_NAME="${CONTAINER_NAME:-pipeline}"
CRON_EXPRESSION="${CRON_EXPRESSION:-0 1 * * *}"
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"

# Container Apps Jobs schedules are evaluated in UTC.
# 0 1 * * * = daily at 01:00 UTC.

PIPELINE_ENV_VARS=(
  "PYTHONUNBUFFERED=1"
)

PIPELINE_SECRETS=()
PIPELINE_SECRET_ENV_VARS=()

log() { echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"; }

ensure_logged_in() {
  if ! az account show --output none >/dev/null 2>&1; then
    log "No Azure CLI session found. Starting device-code login..."
    az login --use-device-code --output none
  fi
  if [[ -n "$AZURE_SUBSCRIPTION_ID" ]]; then
    log "Setting Azure subscription to $AZURE_SUBSCRIPTION_ID"
    az account set --subscription "$AZURE_SUBSCRIPTION_ID"
  fi
}

ensure_resource_group() {
  if az group show --name "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    log "Resource group exists: $RESOURCE_GROUP"
    return
  fi
  log "Creating resource group: $RESOURCE_GROUP"
  az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none
}

ensure_acr() {
  if az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    log "ACR exists: $ACR_NAME"
    return
  fi
  log "Creating ACR: $ACR_NAME"
  az acr create \
    --name "$ACR_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --sku Basic \
    --admin-enabled false \
    --output none
}

ensure_environment() {
  if az containerapp env show --name "$ENV_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    log "Container Apps environment exists: $ENV_NAME"
    return
  fi
  log "Creating Container Apps environment: $ENV_NAME"
  az containerapp env create \
    --name "$ENV_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none
}

ensure_identity() {
  if az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    log "Managed identity exists: $IDENTITY_NAME"
  else
    log "Creating managed identity: $IDENTITY_NAME"
    az identity create \
      --name "$IDENTITY_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --location "$LOCATION" \
      --output none
  fi

  IDENTITY_ID="$(az identity show \
    --name "$IDENTITY_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query id \
    --output tsv)"

  IDENTITY_PRINCIPAL_ID="$(az identity show \
    --name "$IDENTITY_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query principalId \
    --output tsv)"
}

grant_acr_pull() {
  ACR_ID="$(az acr show \
    --name "$ACR_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query id \
    --output tsv)"
  log "Ensuring AcrPull role assignment on ACR"
  az role assignment create \
    --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPull \
    --scope "$ACR_ID" \
    --output none >/dev/null 2>&1 || true
}

build_secret_mapping() {
  local required_vars=("LLM_API_KEY" "LLM_BASE_URL" "LLM_MODEL" "TINYFISH_API_KEY")
  local optional_vars=("LLM_API_VERSION" "TINYFISH_BASE_URL" "DATABASE_URL" "AZURE_STORAGE_CONNECTION_STRING")
  local missing=()
  local name value secret_name

  for name in "${required_vars[@]}"; do
    value="${!name:-}"
    if [[ -z "$value" ]]; then
      missing+=("$name")
      continue
    fi
    secret_name="$(echo "$name" | tr '[:upper:]_' '[:lower:]-')"
    PIPELINE_SECRETS+=("${secret_name}=${value}")
    PIPELINE_SECRET_ENV_VARS+=("${name}=secretref:${secret_name}")
  done

  for name in "${optional_vars[@]}"; do
    value="${!name:-}"
    if [[ -z "$value" ]]; then
      continue
    fi
    secret_name="$(echo "$name" | tr '[:upper:]_' '[:lower:]-')"
    PIPELINE_SECRETS+=("${secret_name}=${value}")
    PIPELINE_SECRET_ENV_VARS+=("${name}=secretref:${secret_name}")
  done

  if [[ "${#missing[@]}" -gt 0 ]]; then
    log "Missing required environment variables for pipeline secrets: ${missing[*]}"
    exit 2
  fi
}

append_optional_env() {
  local name="$1"
  local value="${!name:-}"
  if [[ -n "$value" ]]; then
    PIPELINE_ENV_VARS+=("${name}=${value}")
  fi
}

log "Checking Azure CLI prerequisites..."
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App --output none
az provider register --namespace Microsoft.ContainerRegistry --output none
az provider register --namespace Microsoft.ManagedIdentity --output none

ensure_logged_in
ensure_resource_group
ensure_acr

log "Building and pushing image to ACR..."
az acr build \
  --registry "$ACR_NAME" \
  --image "$IMAGE_NAME" \
  .

IMAGE_REF="${ACR_NAME}.azurecr.io/${IMAGE_NAME}"

ensure_environment
ensure_identity
grant_acr_pull
build_secret_mapping
append_optional_env "BLOB_SYNC_ENABLED"
append_optional_env "BLOB_CONTAINER"
append_optional_env "BLOB_DB_BLOB_NAME"

if az containerapp job show --name "$JOB_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  log "Replacing existing Container Apps job: $JOB_NAME"
  az containerapp job delete \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --yes \
    --output none
fi

log "Creating scheduled Container Apps job: $JOB_NAME"
CREATE_ARGS=(
  --name "$JOB_NAME"
  --resource-group "$RESOURCE_GROUP"
  --environment "$ENV_NAME"
  --trigger-type Schedule
  --cron-expression "$CRON_EXPRESSION"
  --replica-timeout 7200
  --replica-retry-limit 1
  --parallelism 1
  --replica-completion-count 1
  --image "$IMAGE_REF"
  --cpu "1"
  --memory "2Gi"
  --container-name "$CONTAINER_NAME"
  --command "python"
  --args "run_pipeline.py"
  --registry-server "${ACR_NAME}.azurecr.io"
  --mi-user-assigned "$IDENTITY_ID"
  --registry-identity "$IDENTITY_ID"
)

if [ "${#PIPELINE_SECRETS[@]}" -gt 0 ]; then
  CREATE_ARGS+=(--secrets "${PIPELINE_SECRETS[@]}")
fi

if [ "${#PIPELINE_ENV_VARS[@]}" -gt 0 ]; then
  CREATE_ARGS+=(--env-vars "${PIPELINE_ENV_VARS[@]}")
fi

if [ "${#PIPELINE_SECRET_ENV_VARS[@]}" -gt 0 ]; then
  CREATE_ARGS+=(--env-vars "${PIPELINE_SECRET_ENV_VARS[@]}")
fi

az containerapp job create "${CREATE_ARGS[@]}" --output none

log "Deployment complete."
echo "Resource Group: $RESOURCE_GROUP"
echo "Environment:    $ENV_NAME"
echo "Job:            $JOB_NAME"
echo "Image:          $IMAGE_REF"
echo "Schedule:       $CRON_EXPRESSION (UTC)"
echo
echo "Manual trigger:"
echo "  az containerapp job start --name $JOB_NAME --resource-group $RESOURCE_GROUP"
echo
echo "Executions:"
echo "  az containerapp job execution list --name $JOB_NAME --resource-group $RESOURCE_GROUP --output table"
echo
echo "Logs:"
echo "  az containerapp job logs show --name $JOB_NAME --resource-group $RESOURCE_GROUP --container $CONTAINER_NAME --tail 100"
