# Self-Hosted n8n

This document owns all n8n-specific setup and update instructions.

For EC2/server setup, SSH, Docker install, repo clone, and backend deployment, see [EC2 setup](EC2_SETUP.md).

For shared prompt loading behavior, see [Prompt Loader](PROMPT_LOADER.md).

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

## Required Env Vars

```env
N8N_ENCRYPTION_KEY=long-random-secret
N8N_IMAGE=n8nio/n8n:1.84.3
N8N_SCOUT_WEBHOOK_URL=http://n8n:5678/webhook/scout
N8N_MERIDIAN_WEBHOOK_URL=http://n8n:5678/webhook/meridian
```

Generate the encryption key:

```bash
openssl rand -hex 32
```

Keep `N8N_ENCRYPTION_KEY` stable. If it changes, saved n8n credentials may stop decrypting.

## Local n8n Setup

From repo root:

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Open:

```text
http://localhost:5678
```

First-time setup:

```text
1. Create n8n admin account.
2. Import n8n/scout.json.
3. Import n8n/meridian.json.
4. Activate both workflows.
5. Verify webhook paths:
   - scout
   - meridian
```

Local service URLs:

```text
FastAPI: http://localhost:8000
n8n:     http://localhost:5678
```

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

n8n workflows load system prompts through the shared [Prompt Loader](PROMPT_LOADER.md):

```text
http://api:8000/prompts/scout/json
http://api:8000/prompts/meridian/json
```

Inside Docker Compose, `api` is the FastAPI service name, so n8n can call `http://api:8000`.

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
docker compose up -d --build api
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
