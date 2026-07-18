# Render FastAPI Deployment

This document records the FastAPI move from EC2 to Render.

Current split:

```text
UI on Vercel
  -> FastAPI on Render
      -> n8n webhooks on EC2
          -> n8n Postgres on EC2
```

n8n remains the default engine and stays self-hosted on EC2. LangGraph runs inside FastAPI only when it is selected manually; see [Agent engine selection](AGENT_ENGINE.md).

## Files Changed For Render

```text
Dockerfile
  -> uses Render's PORT environment variable when present

render.yaml
  -> defines the Render web service
  -> selects AGENT_ENGINE=n8n by default
  -> declares the LangGraph model and secret configuration

twm/shared/properties/properties.ini
  -> stores common FastAPI config in the [APP] section

twm/shared/properties/properties-prod.ini
  -> stores prod FastAPI overrides in the [APP] section

docker-compose.yml
  -> runs only postgres + n8n
  -> does not read FastAPI property files

.gitignore
  -> ignores n8n.env for EC2 n8n config

n8n.env.example
  -> EC2 n8n environment template
```

## Render Service

Render can deploy from the Dockerfile using [../render.yaml](../render.yaml).

Service:

```text
name: travelwithme-api
runtime: docker
health check: /health
```

Required Render environment variable:

```properties
ENVIRONMENT=prod
AGENT_ENGINE=n8n
LANGGRAPH_MODEL=openai/gpt-oss-120b
GROQ_API_KEY=<secret>
```

FastAPI config is loaded from:

```text
twm/shared/properties/properties.ini
twm/shared/properties/properties-{ENVIRONMENT}.ini
```

`twm/shared/properties/properties.ini` is the committed base FastAPI config. `twm/shared/properties/properties-prod.ini` is the committed Render/prod overlay. For a future environment, add `twm/shared/properties/properties-<env>.ini` and set `ENVIRONMENT=<env>`.

Render sets `PORT` automatically. The Dockerfile starts FastAPI on `${PORT:-8000}`.

## API Health Check

After deploy, check:

```text
https://<render-service-host>/health
```

Expected:

```json
{"status":"ok"}
```

## UI Configuration

The frontend should use the Render FastAPI base URL:

```text
https://<render-service-host>
```

UI calls should remain:

```text
POST https://<render-service-host>/scout
POST https://<render-service-host>/meridian
```

The UI should not call n8n directly.

## Selected Engine Requirements

With `AGENT_ENGINE=langgraph`, execution stays inside FastAPI and requires `GROQ_API_KEY`.

With `AGENT_ENGINE=n8n`, FastAPI calls authenticated n8n webhooks through HTTPS:

```text
https://n8n.example.com/webhook/scout
https://n8n.example.com/webhook/meridian
```

Configure `N8N_WEBHOOK_TOKEN` on Render and the matching Header Auth credential in n8n. Port 5678 remains bound to loopback; the reverse proxy exposes only HTTPS. Restrict editor access independently from webhook access.

Changing `AGENT_ENGINE` requires a Render restart/redeploy. Follow [Agent engine selection](AGENT_ENGINE.md) to switch, verify, or roll back.
