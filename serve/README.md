# DealSignal MVP

DealSignal is a sourcing pipeline that discovers public web content for watchlist companies, extracts strategic signals with Azure OpenAI, stores structured events, deduplicates them, scores them, and publishes a daily digest.

## Architecture

```text
Watchlist YAML
    |
    v
[Discover] --(TinyFish or Basic web provider)--> sources table
    |
    v
[Fetch] --> raw article text in data/raw/{sha256}.txt
    |
    v
[Extract + Dedup + Score] --(Azure OpenAI)--> signal_events table
    |
    v
[Digest] --> reports/daily_digest.md
    |
    v
FastAPI UI (/ , /companies , /companies/{id} , /events/{id})
```

## Project Layout

This project is split at the parent level:

- `../serve`: full local runtime (UI + pipeline + models + tests)
- `../azure_artifacts`: ACA deployment/build assets
- `../.env`: shared environment variables

`dealsignal/agents`: provider abstraction, TinyFish provider, fallback requests+BeautifulSoup provider  
`dealsignal/pipeline`: discover, fetch, extract, score, digest  
`dealsignal/models`: SQLAlchemy models and database session  
`dealsignal/app`: FastAPI app and Jinja2 templates  
`run_pipeline.py`: non-interactive batch entrypoint for scheduled job runs  
`config/watchlist.yaml`: target company watchlist (supports `name`, `execs`, `themes`, `aliases`, `sector`)  
`config/source_policy.yaml`: allow/block domain and strategic-term filtering for discovery  
`data/raw`: raw article content archive  
`reports/daily_digest.md`: top opportunities digest  
`tests`: unit tests for score/fingerprint/dedup

## Requirements

- Python 3.11+
- Azure OpenAI deployment
- Optional TinyFish key for primary browsing

## Setup

```bash
uv sync --dev
```

Update `.env` in project root:

```bash
LLM_API_KEY=...
LLM_BASE_URL=https://<your-azure-openai-resource>.openai.azure.com
LLM_MODEL=<your-deployment-name>
LLM_API_VERSION=2024-02-15-preview

TINYFISH_API_KEY=...
TINYFISH_BASE_URL=https://api.tinyfish.ai
```

If `TINYFISH_API_KEY` is missing, DealSignal automatically falls back to the basic provider.

Watchlist schema example:

```yaml
companies:
  - name: Stripe
    execs:
      - Patrick Collison
      - John Collison
    themes:
      - payments
      - enterprise expansion
      - strategic partnerships
    aliases:
      - Stripe Inc.
    sector: Fintech
```

## Run Pipeline

```bash
uv run python main.py run-pipeline
```

or

```bash
python run_pipeline.py
```

This runs:
1. discovery for watchlist companies
2. fetch and text archival
3. extraction with Azure OpenAI
4. deduplication via event fingerprint
5. scoring using confidence/strength/recency
6. digest generation at `reports/daily_digest.md`

## Start Web UI

```bash
uv run python main.py serve
```

Open `http://127.0.0.1:8000`.

## Test

```bash
uv run pytest -q
```

## Run Overnight In Azure Container Apps Jobs

This repo includes a containerized batch entrypoint and a deployment script for Azure Container Apps Jobs.

### Files

- `../azure_artifacts/Dockerfile`: packages the pipeline as a job container
- `requirements.txt`: pinned Python dependencies for the image build
- `.dockerignore`: keeps local state and secrets out of the build context
- `../azure_artifacts/deploy.sh`: provisions Azure resources, pushes the image, and creates the scheduled job

### How The Job Runs

The container starts with:

```bash
python run_pipeline.py
```

The job is a finite batch workload. When the Python process exits, the container exits. That is the correct behavior for Azure Container Apps Jobs.

### Schedule

The deployment script configures a scheduled Container Apps Job with:

- trigger type: `Schedule`
- cron expression: `0 1 * * *`
- start time: `01:00 UTC` every day
- timeout: `7200` seconds
- cpu: `1`
- memory: `2Gi`
- parallelism: `1`
- replica completion count: `1`

### Required Environment Variables

Export these in your shell before running `../azure_artifacts/deploy.sh`.  
The script automatically creates Container Apps job secrets and maps them to env vars.

```bash
export LLM_API_KEY="..."
export LLM_BASE_URL="https://<your-azure-openai-resource>.openai.azure.com/"
export LLM_MODEL="<your-azure-deployment-name>"
export TINYFISH_API_KEY="..."

# Optional
export LLM_API_VERSION="2024-02-15-preview"
export TINYFISH_BASE_URL="https://agent.tinyfish.ai"
export DATABASE_URL="sqlite:///./dealsignal.db"
```

For production, prefer a persistent database over local SQLite.

### Deploy

Run from a bash shell:

```bash
chmod +x ../azure_artifacts/deploy.sh
../azure_artifacts/deploy.sh
```

If you are not already authenticated, the script runs:

```bash
az login --use-device-code
```

You can pin a subscription without using the portal:

```bash
export AZURE_SUBSCRIPTION_ID="<subscription-guid>"
```

You can override defaults:

```bash
export RESOURCE_GROUP="rg-dealsignal-prod"
export LOCATION="eastus"
export ACR_NAME="dealsignalacr12345"
export ENV_NAME="dealsignal-env"
export JOB_NAME="dealsignal-nightly"
export IMAGE_NAME="dealsignal-pipeline:latest"
../azure_artifacts/deploy.sh
```

### Manual Trigger

```bash
az containerapp job start \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP"
```

### Inspect Executions

```bash
az containerapp job execution list \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --output table \
  --query '[].{Status: properties.status, Name: name, StartTime: properties.startTime}'
```

### View Logs

```bash
az containerapp job logs show \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --container "pipeline" \
  --tail 100
```

For a specific execution:

```bash
JOB_EXECUTION=$(az containerapp job execution list \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[0].name" \
  --output tsv)

az containerapp job logs show \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --execution "$JOB_EXECUTION" \
  --container "pipeline" \
  --tail 200
```

### Monitoring

The deployment script creates a Container Apps environment and scheduled job with managed identity.

Recommended operational setup:

- keep the pipeline idempotent so reruns do not duplicate data
- log structured events to stdout/stderr
- create Azure Monitor alerts for failed job executions
- use managed identity for ACR pulls
- keep credentials in Container Apps secrets, not in the image
