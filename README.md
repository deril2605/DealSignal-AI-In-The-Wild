# DealSignal Workspace

Workspace layout:

- `serve/`: full local runtime, UI, pipeline, tests, configs
- `azure_artifacts/`: Azure Container Apps build and deploy assets
- `.env`: shared environment variables
- `.env.example`: template for local and cloud configuration

## Local Run

```bash
cd serve
uv sync --dev
uv run python main.py serve
```

Local UI starts at `http://127.0.0.1:8000`.

## Azure Deployment

Deploy from the workspace root:

```bash
chmod +x azure_artifacts/deploy.sh
./azure_artifacts/deploy.sh
```

The ACA job builds from `serve/` and runs `python run_pipeline.py`.

## Shared State

When blob sync is enabled in `.env`, the system persists:

- SQLite database to Blob
- raw fetched article text to Blob

Local `serve` downloads the latest SQLite snapshot on startup so the UI reflects cloud-generated state.

## More Detail

See [serve/README.md](C:\Users\deril\OneDrive\Desktop\Deril\Development\DealSignal-AI-in-the-Wild\serve\README.md) for runtime details and [azure_artifacts/deploy.sh](C:\Users\deril\OneDrive\Desktop\Deril\Development\DealSignal-AI-in-the-Wild\azure_artifacts\deploy.sh) for deployment behavior.
