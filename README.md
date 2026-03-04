# DealSignal MVP

DealSignal is an always-on sourcing agent that discovers public web content for watchlist companies, extracts strategic signals with Azure OpenAI, stores structured events, deduplicates them, scores them, and publishes a daily digest.

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

`dealsignal/agents`: provider abstraction, TinyFish provider, fallback requests+BeautifulSoup provider  
`dealsignal/pipeline`: discover, fetch, extract, score, digest  
`dealsignal/models`: SQLAlchemy models and database session  
`dealsignal/app`: FastAPI app and Jinja2 templates  
`config/watchlist.yaml`: target company watchlist  
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

## Run Pipeline

```bash
uv run python main.py run-pipeline
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
