# DealSignal Serve

DealSignal is a watchlist-driven sourcing pipeline plus local UI. It discovers public sources for target companies, fetches article text, extracts structured strategic signals with Azure OpenAI, deduplicates events, scores them, and stores the results in SQLite.

For `MVP v0`, the primary output is the structured event database and the UI. `reports/daily_digest.md` is generated as an internal artifact, not treated as a delivery channel.

## Current Structure

The parent workspace is split into:

- `../serve`: full local runtime
- `../azure_artifacts`: Azure Container Apps build and deploy assets
- `../.env`: shared environment variables

Within `serve/`:

- `dealsignal/agents`: provider implementations
- `dealsignal/pipeline`: discover, fetch, extract, score, digest
- `dealsignal/models`: SQLAlchemy models and database setup
- `dealsignal/app`: FastAPI app and templates
- `config/watchlist.yaml`: watchlist definition
- `config/source_policy.yaml`: source filtering policy
- `data/raw`: local raw article cache
- `reports/daily_digest.md`: generated digest artifact
- `tests`: unit tests

## Requirements

- Python 3.11+
- `uv` recommended for local dependency management
- Azure OpenAI deployment
- TinyFish API key
- Optional Azure Blob Storage for cross-run persistence

## Local Setup

Run from `serve/`:

```bash
uv sync --dev
```

Environment variables live in the parent `.env` file. `main.py` and `run_pipeline.py` load `../.env` automatically.

Important variables:

```bash
LLM_API_KEY=...
LLM_BASE_URL=https://<your-azure-openai-resource>.openai.azure.com/
LLM_MODEL=<your-azure-deployment-name>
LLM_API_VERSION=2024-02-15-preview

TINYFISH_API_KEY=...
TINYFISH_BASE_URL=https://agent.tinyfish.ai
TINYFISH_MAX_AGENTS=2

DATABASE_URL=sqlite:///./dealsignal.db

BLOB_SYNC_ENABLED=true
AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=<storage-account>;AccountKey=<storage-key>;EndpointSuffix=core.windows.net"
BLOB_CONTAINER=dealsignal
BLOB_DB_BLOB_NAME=state/dealsignal.db
BLOB_RAW_PREFIX=raw
```

## Local UI

Run from `serve/`:

```bash
uv run python main.py serve
```

Open `http://127.0.0.1:8000`.

On startup, local `serve` pulls the latest SQLite snapshot from Blob when blob sync is configured.

Available pages:

- `/`: signal list and filters
- `/companies`
- `/companies/{id}`
- `/events/{id}`
- `/admin`: pipeline telemetry and ACA metadata

## Pipeline Run

Run from `serve/`:

```bash
uv run python main.py run-pipeline
```

or:

```bash
python run_pipeline.py
```

Pipeline stages:

1. load watchlist
2. discover candidate URLs
3. fetch and archive raw text
4. extract structured signals with Azure OpenAI
5. dedupe by event fingerprint
6. score events
7. write digest artifact

## Persistence Model

Local and ACA share state through Azure Blob Storage when enabled.

Persisted today:

- SQLite database blob: `state/dealsignal.db`
- raw fetched article blobs: `raw/<sha>.txt`

Not treated as persistent system-of-record output for `v0`:

- `reports/daily_digest.md`

## Azure Container Apps Job

ACA assets live in `../azure_artifacts/`.

The ACA job:

- builds from the `serve/` directory
- runs `python run_pipeline.py`
- persists DB state to Blob at the end of each run
- uploads fetched raw text files to Blob

Default schedule is controlled by `CRON_EXPRESSION` in `.env`. ACA cron is interpreted in UTC.

Deploy from the parent project root:

```bash
chmod +x azure_artifacts/deploy.sh
./azure_artifacts/deploy.sh
```

Manual trigger:

```bash
az containerapp job start --name "$JOB_NAME" --resource-group "$RESOURCE_GROUP"
```

Stream logs:

```bash
EXEC=$(az containerapp job execution list --name "$JOB_NAME" --resource-group "$RESOURCE_GROUP" --query "[0].name" -o tsv)
az containerapp job logs show --name "$JOB_NAME" --resource-group "$RESOURCE_GROUP" --execution "$EXEC" --container pipeline --follow
```

## Tests

Run from `serve/`:

```bash
uv run pytest -q
```
