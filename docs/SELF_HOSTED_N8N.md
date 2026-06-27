# Self-Hosted n8n

This document owns all n8n-specific setup and update instructions.

For EC2/server setup, SSH, Docker install, repo clone, and n8n deployment, see [EC2 setup](EC2_SETUP.md).

FastAPI now runs on Render. See [Render FastAPI deployment](RENDER_FASTAPI.md).

## What n8n Does

n8n is the current orchestration layer for Trip Matcher.

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
N8N_HOST=13.201.32.120
N8N_PORT=5678
N8N_PROTOCOL=http
N8N_SECURE_COOKIE=false
WEBHOOK_URL=http://13.201.32.120:5678/
N8N_EDITOR_BASE_URL=http://13.201.32.120:5678/
N8N_ENCRYPTION_KEY=long-random-secret
API_BASE_URL=https://<render-service-host>
```

FastAPI-specific config belongs in `twm/shared/properties/properties.ini` / `twm/shared/properties/properties-{ENVIRONMENT}.ini`. `agent_engine`, `n8n_scout_webhook_url`, and `n8n_meridian_webhook_url` are FastAPI settings, not EC2 n8n settings.

Generate the encryption key:

```bash
openssl rand -hex 32
```

Keep `N8N_ENCRYPTION_KEY` stable. If it changes, saved n8n credentials may stop decrypting.

## EC2 n8n Setup

Current EC2 n8n URL:

```text
http://13.201.32.120:5678
```

For EC2, n8n public URL values should point to the EC2 public IP:

```yaml
- N8N_HOST=13.201.32.120
- N8N_PORT=5678
- N8N_PROTOCOL=http
- N8N_SECURE_COOKIE=false
- WEBHOOK_URL=http://13.201.32.120:5678/
- N8N_EDITOR_BASE_URL=http://13.201.32.120:5678/
- N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}
```

Later with domain + HTTPS:

```yaml
- N8N_HOST=n8n.yourdomain.com
- N8N_PROTOCOL=https
- N8N_SECURE_COOKIE=true
- WEBHOOK_URL=https://n8n.yourdomain.com/
- N8N_EDITOR_BASE_URL=https://n8n.yourdomain.com/
```

Initial EC2 n8n setup:

```text
1. Open http://13.201.32.120:5678.
2. Create n8n admin account.
3. Import n8n/scout.json.
4. Import n8n/meridian.json.
5. Activate both workflows.
6. Verify webhook paths:
   - scout
   - meridian
```

## Prompt Loading

n8n workflows do not call a prompt API.

FastAPI on Render loads prompt files locally, then sends prompt text in the n8n webhook payload.

n8n reads:

```text
Webhook body.prompt
```

as the LangChain agent system message.

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
1. Open http://13.201.32.120:5678.
2. Edit workflow in n8n UI.
3. Save.
4. Activate.
```

At this point the workflow is live.

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
http://13.201.32.120:5678
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
