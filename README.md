# DealSignal Workspace

Top-level layout:

- `serve/`: complete local application runtime (UI + pipeline + tests)
- `azure_artifacts/`: Azure Container Apps build/deploy assets
- `.env`: shared environment variables

## Local UI / Pipeline

```bash
cd serve
uv sync --dev
uv run python main.py serve
```

## ACA Deployment

```bash
chmod +x azure_artifacts/deploy.sh
./azure_artifacts/deploy.sh
```
