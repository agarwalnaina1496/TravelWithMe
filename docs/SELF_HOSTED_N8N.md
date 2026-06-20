# Self-Hosted n8n Setup

The UI should call the FastAPI service.
FastAPI should forward to local n8n webhooks.
n8n remains the visual workflow layer.

## Local stack

Bring up the stack with:

```bash
docker compose up --build
```

Before running, copy `.env.example` to `.env`.

Services:

- FastAPI: `http://localhost:8000`
- n8n editor: `http://localhost:5678`

## Import workflows

Import these files into local n8n:

- `n8n/scout.json`
- `n8n/meridian.json`

After import, verify these webhook paths:

- `scout`
- `meridian`

If n8n assigns different webhook URLs, update the API env vars to match.

## Env vars

- `N8N_SCOUT_WEBHOOK_URL`
- `N8N_MERIDIAN_WEBHOOK_URL`
- `N8N_IMAGE`
- `N8N_SECURE_COOKIE=false`

Example values when running locally:

- `n8nio/n8n:1.84.3`
- `http://n8n:5678/webhook/scout`
- `http://n8n:5678/webhook/meridian`
- `false`

## Flow

UI -> FastAPI `/scout` or `/meridian` -> n8n webhook -> workflow -> response

## Notes

- Keep the FastAPI layer thin.
- Keep business logic inside the n8n workflows unless you intentionally move it to another engine later.
- The engine boundary in FastAPI exists so a future LangGraph migration is a backend swap, not an API rewrite.
