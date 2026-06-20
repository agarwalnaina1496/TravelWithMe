# TravelWithMe
Helping you choose your destination and plan your trip with confidence.

## What this repo provides

- FastAPI service exposing `POST /scout` and `POST /meridian`
- n8n self-hosted stack for the visual workflow layer
- Postgres for n8n persistence

## Run locally

Copy `.env.example` to `.env`, then run:

```bash
docker compose up --build
```

FastAPI:
- `http://localhost:8000/`
- `http://localhost:8000/health`

n8n:
- `http://localhost:5678/`

If this is your first n8n boot, create the `admin` account in the browser and then import the workflow JSON files from `n8n/`.
The default n8n image is pinned through `N8N_IMAGE` so the stack does not drift with `latest`.

## Import n8n workflows

The workflow exports live in the repo here:

- `n8n/scout.json`
- `n8n/meridian.json`

Import them into your self-hosted n8n instance, then confirm the webhook paths remain:

- `scout`
- `meridian`

## Architecture

- UI calls `POST /scout` and `POST /meridian`
- n8n stays the visual orchestrator
- FastAPI stays minimal and swappable
- Later, the engine behind FastAPI can move to LangGraph without changing the public API
