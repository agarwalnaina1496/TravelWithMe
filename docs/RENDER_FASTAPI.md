# Render FastAPI Deployment

This document records the FastAPI move from EC2 to Render.

Current split:

```text
UI on Vercel
  -> FastAPI on Render
      -> n8n webhooks on EC2
          -> n8n Postgres on EC2
```

n8n remains self-hosted on EC2. Only the FastAPI service moves to Render.

## Files Changed For Render

```text
Dockerfile
  -> uses Render's PORT environment variable when present

render.yaml
  -> defines the Render web service and sets ENVIRONMENT=prod

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

So EC2 security group access to port `5678` must remain available to Render unless n8n is later placed behind a domain, reverse proxy, or allowlist.
