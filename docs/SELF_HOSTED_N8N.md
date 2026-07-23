# Self-Hosted n8n

This document owns all n8n-specific setup and update instructions.

For EC2/server setup, SSH, Docker install, repo clone, and n8n deployment, see [EC2 setup](EC2_SETUP.md).

FastAPI now runs on Render. See [Render FastAPI deployment](RENDER_FASTAPI.md).

## What n8n Does

n8n is the default thin model-invocation adapter for Trip Matcher. FastAPI owns prompt preparation, parsing, Pydantic validation, the single repair attempt, and public response normalization. LangGraph remains available through an explicit manual configuration switch; see [Agent engine selection](AGENT_ENGINE.md).

Each agent workflow has three logical nodes:

```text
Webhook -> Agent (+ model connection) -> Respond to Webhook
```

Each Agent has a Structured Output Parser subnode fed by `body.output_schema`. On n8n 1.84.3 the Tools Agent exposes that schema to the model as the supported final-format tool. FastAPI still owns canonical JSON parsing, Pydantic semantic validation, repair policy, and public normalization. The response is `{"raw_output":"<serialized schema-constrained object>"}`.

Both workflows set a 180-second execution timeout. FastAPI waits 185 seconds for n8n, so the workflow terminates before the caller deadline instead of completing after FastAPI has already returned a timeout.

Current workflows:

- [Scout workflow](../n8n/scout.json)
- [Meridian workflow](../n8n/meridian.json)

Postgres is used only for n8n persistence:

```text
workflows
credentials
execution data
```

## Required n8n Env File

EC2 n8n uses `n8n.env`, not the FastAPI property files under `twm/shared/properties/`.

```properties
N8N_HOST=n8n.example.com
N8N_PORT=5678
N8N_PROTOCOL=https
N8N_SECURE_COOKIE=true
WEBHOOK_URL=https://n8n.example.com/
N8N_EDITOR_BASE_URL=https://n8n.example.com/
N8N_ENCRYPTION_KEY=long-random-secret
N8N_DB_PASSWORD=long-random-secret
API_BASE_URL=https://<render-service-host>
```

FastAPI-specific config belongs in `twm/shared/properties/properties.ini` / `twm/shared/properties/properties-{ENVIRONMENT}.ini`. `agent_engine`, `n8n_scout_webhook_url`, and `n8n_meridian_webhook_url` are FastAPI settings, not EC2 n8n settings.

Generate the encryption key:

```bash
openssl rand -hex 32
```

Keep `N8N_ENCRYPTION_KEY` stable. If it changes, saved n8n credentials may stop decrypting.

## EC2 n8n Setup

Production n8n URL:

```text
https://n8n.example.com
```

Publish n8n only through an HTTPS reverse proxy:

```yaml
- N8N_HOST=n8n.example.com
- N8N_PORT=5678
- N8N_PROTOCOL=https
- N8N_SECURE_COOKIE=true
- WEBHOOK_URL=https://n8n.example.com/
- N8N_EDITOR_BASE_URL=https://n8n.example.com/
- N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}
- DB_POSTGRESDB_PASSWORD=${N8N_DB_PASSWORD}
```

Initial EC2 n8n setup:

```text
1. Open the restricted HTTPS editor URL.
2. Create n8n admin account.
3. Import n8n/scout.json.
4. Import n8n/meridian.json.
5. Create the `TWM webhook auth` Header Auth credential using header `X-TWM-Webhook-Token`, and attach it to both Webhook nodes.
6. Activate both workflows.
7. Verify webhook paths:
   - scout
   - meridian
```

## Prompt Loading

n8n workflows do not call a prompt API.

FastAPI on Render loads prompt files locally, then sends prompt text in the n8n webhook payload.

n8n reads:

```text
Webhook body.system_prompt
Webhook body.user_prompt
Webhook body.output_schema
```

The agent uses these as its system and user messages and constrains generation with the supplied schema. Respond to Webhook serializes that object as `raw_output` for FastAPI's common parser and semantic validator.

## Updating n8n Workflows

n8n has two places to think about:

```text
Live workflow: stored in n8n/Postgres on EC2
Versioned backup: n8n/*.json in GitHub
```

Changing [n8n/scout.json](../n8n/scout.json) or [n8n/meridian.json](../n8n/meridian.json) in GitHub does not automatically update the live n8n workflow.

### Recommended Flow

Edit live workflow first:

```text
1. Open the restricted HTTPS n8n editor.
2. Edit workflow in n8n UI.
3. Save.
4. Activate.
```

At this point the workflow is live.

Because this private contract changes in coordination with FastAPI, import and activate both updated workflows immediately before deploying the matching Backend version. If the Backend deploy must be rolled back, also restore both previous workflow exports.

Then sync repo for versioning:

```text
1. Export workflow JSON from n8n UI.
2. Replace n8n/scout.json or n8n/meridian.json locally.
3. Commit and push.
```

Commands:

```bash
git add n8n/scout.json n8n/meridian.json
git commit -m "Update n8n workflows"
git push
```

### If Workflow JSON Was Changed Locally First

```text
1. Commit and push JSON changes.
2. EC2: git pull.
3. Open EC2 n8n UI.
4. Import updated JSON.
5. Save and activate workflow.
```

On EC2:

```bash
cd ~/TravelWithMe
git pull origin master
docker compose up -d
```

Then import in n8n UI:

```text
https://n8n.example.com
```

## n8n Docker Commands

Restart n8n:

```bash
docker compose restart n8n
```

View logs:

```bash
docker compose logs --tail=100 n8n
```

Check containers:

```bash
docker compose ps
```

## Backup And Safety

n8n data lives in Docker volumes:

```text
n8n_data
n8n_postgres_data
```

Safe commands:

```bash
docker compose restart n8n
docker compose up -d
```

Do not run unless intentionally deleting n8n/Postgres data:

```bash
docker compose down -v
docker volume rm ...
```

Manual Postgres backup for n8n:

```bash
docker compose exec postgres pg_dump -U n8n n8n > n8n-backup.sql
```

Workflow backup:

```text
n8n UI -> Export workflow JSON -> commit to GitHub
```

## n8n Rollback

```text
1. Get previous n8n/*.json from GitHub.
2. Import it in n8n UI.
3. Save.
4. Activate.
```
