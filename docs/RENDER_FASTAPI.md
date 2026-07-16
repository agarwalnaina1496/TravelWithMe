# Render FastAPI Deployment

This document records the FastAPI move from EC2 to Render.

Current split:

```text
UI on Vercel
  -> FastAPI on Render
      -> n8n webhooks on EC2
          -> n8n Postgres on EC2
```

n8n remains self-hosted on EC2. TWM-54 adds LangGraph runtime prerequisites but keeps n8n selected; see [Agent engine foundation](AGENT_ENGINE.md).

## Files Changed For Render

```text
Dockerfile
  -> uses Render's PORT environment variable when present

render.yaml
  -> defines the Render web service
  -> keeps AGENT_ENGINE=n8n during foundation and parity work
  -> records the planned LangGraph model without storing credentials

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

## n8n Requirement

FastAPI on Render calls n8n on EC2 through:

```text
http://13.201.32.120:5678/webhook/scout
http://13.201.32.120:5678/webhook/meridian
```

EC2 access to the n8n webhook port must remain available to Render unless n8n is placed behind a domain, reverse proxy, or allowlist.

Do not select `AGENT_ENGINE=langgraph` until TWM-56 delivers and verifies the concrete Scout and Meridian graphs.
