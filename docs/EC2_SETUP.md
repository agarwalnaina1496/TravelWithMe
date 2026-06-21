# EC2 Deployment Setup

This document records how TravelWithMe was deployed on AWS EC2.

For the platform-agnostic architecture, including fixed vs swappable parts and future KB/LangGraph direction, use:

[Architecture](ARCHITECTURE.md)

For n8n-specific setup, workflow updates, backups, and rollback, use:

[Self-Hosted n8n](SELF_HOSTED_N8N.md)

## What Is Deployed

```text
AWS EC2 Ubuntu server
  -> Docker Compose
      -> FastAPI container
      -> n8n container
      -> Postgres container for n8n only
```

The UI should call FastAPI:

```text
http://13.201.32.120:8000
```

n8n is used as the internal workflow/orchestration layer:

```text
http://13.201.32.120:5678
```

Postgres is not currently the app knowledge base. It is only used by n8n to persist workflows, credentials, and execution data.

## EC2 Instance Details

```text
Provider: AWS EC2
AMI: Ubuntu Server 24.04 LTS (HVM), SSD Volume Type
Architecture: 64-bit x86 / amd64
Instance type: t3.micro
Storage: 20 GiB EBS
Public IPv4: 13.201.32.120
User: ubuntu
Local PEM path: /c/Users/agarw/Desktop/TWM/twm-key.pem
```

`t3.micro` is okay for testing/MVP. For smoother n8n usage, `t3.small` is preferred.

## Security Group Rules

Current testing rules:

```text
22    SSH      your IP only
80    HTTP     0.0.0.0/0
443   HTTPS    0.0.0.0/0
8000  FastAPI  your IP only
5678  n8n      your IP only
```

Later, after domain + HTTPS reverse proxy:

```text
22   SSH    your IP only
80   HTTP   0.0.0.0/0
443  HTTPS  0.0.0.0/0
```

At that point, remove direct public access to `8000` and `5678`.

## Connect To EC2

From local Git Bash:

```bash
ssh -i /c/Users/agarw/Desktop/TWM/twm-key.pem ubuntu@13.201.32.120
```

After connecting, the terminal prompt should look similar to:

```text
ubuntu@ip-172-31-xx-xx:~$
```

That is the EC2 terminal. Commands run there execute on the server, not on the local laptop.

## Docker Setup On EC2

Docker was installed on EC2 with:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
```

## Private GitHub Repo Access

The repo is private, so EC2 needs permission to clone/pull it.

We used a GitHub deploy key.

### 1. Generate Deploy Key On EC2

Run this on EC2:

```bash
ssh-keygen -t ed25519 -C "ec2-travelwithme" -f ~/.ssh/travelwithme_deploy
```

Passphrase can be left blank for server deploy usage.

### 2. Copy Public Key

Run this on EC2:

```bash
cat ~/.ssh/travelwithme_deploy.pub
```

Copy the full output. It starts with:

```text
ssh-ed25519 ...
```

### 3. Add Key In GitHub

In GitHub:

```text
Repo -> Settings -> Deploy keys -> Add deploy key
```

Use:

```text
Title: EC2 TravelWithMe
Key: copied public key
Allow write access: unchecked
```

Write access is not needed because EC2 only pulls code.

### 4. Configure SSH On EC2

Create/edit:

```bash
nano ~/.ssh/config
```

Add:

```sshconfig
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/travelwithme_deploy
  IdentitiesOnly yes
```

Set permissions:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/travelwithme_deploy
chmod 600 ~/.ssh/config
```

Test:

```bash
ssh -T git@github.com
```

Expected result is an authentication success message from GitHub.

## Clone Repo On EC2

Use the SSH repo URL, not HTTPS.

GitHub repo: [agarwalnaina1496/TravelWithMe](https://github.com/agarwalnaina1496/TravelWithMe)

```bash
git clone git@github.com:agarwalnaina1496/TravelWithMe.git TravelWithMe
cd TravelWithMe
```

Repo is expected at:

```text
~/TravelWithMe
```

## Environment Setup

Create `.env` on EC2:

```bash
cd ~/TravelWithMe
cp .env.example .env
nano .env
```

For n8n-specific env vars, encryption key, and public URL settings, see [Self-Hosted n8n](SELF_HOSTED_N8N.md).

Do not commit `.env`.

## Start The Stack

On EC2:

```bash
cd ~/TravelWithMe
docker compose up -d --build
docker compose ps
```

Check API:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok"}
```

## Live URLs

From browser:

```text
FastAPI health:
http://13.201.32.120:8000/health

n8n:
http://13.201.32.120:5678
```

UI base API URL:

```text
http://13.201.32.120:8000
```

UI should call:

```text
POST http://13.201.32.120:8000/scout
POST http://13.201.32.120:8000/meridian
```

## n8n Setup And Updates

n8n is running on the same EC2 instance, but n8n-specific setup and workflow operations live in [Self-Hosted n8n](SELF_HOSTED_N8N.md).

Use that doc for:

```text
- n8n local setup
- n8n EC2 setup
- workflow import/export
- workflow updates
- n8n backup
- n8n rollback
```

## FastAPI Update Flow On EC2

Use this when code under `twm/`, `Dockerfile`, `requirements.txt`, or API behavior changes.

On local machine:

```bash
git status
git add .
git commit -m "Update FastAPI"
git push
```

On EC2:

```bash
ssh -i /c/Users/agarw/Desktop/TWM/twm-key.pem ubuntu@13.201.32.120
cd ~/TravelWithMe
git pull origin master
docker compose up -d --build api
docker compose ps
docker compose logs --tail=50 api
curl http://localhost:8000/health
```

If the default branch is `main`, use:

```bash
git pull origin main
```

## Prompt Update Flow On EC2

Shared prompt loading behavior is documented in [Prompt Loader](PROMPT_LOADER.md).

Prompt files:

- [Scout system prompt](../twm/prompts/SCOUT_SYSTEM_PROMPT.md)
- [Meridian system prompt](../twm/prompts/MERIDIAN_SYSTEM_PROMPT.md)

On local machine:

```bash
git add twm/prompts
git commit -m "Update prompts"
git push
```

On EC2:

```bash
cd ~/TravelWithMe
git pull origin master
docker compose up -d --build api
```

## EC2 Testing

Health:

```bash
curl http://localhost:8000/health
```

Scout:

```bash
curl -X POST http://localhost:8000/scout \
  -H "Content-Type: application/json" \
  -d '{"trip_state": {}, "message": "I want a 3 night trip from Bengaluru"}'
```

Meridian:

```bash
curl -X POST http://localhost:8000/meridian \
  -H "Content-Type: application/json" \
  -d '{"trip_context": {"required_inputs": {"origin_city": "Bengaluru", "budget": 30000, "budget_unit": "total", "duration_nights": 3, "num_travelers": 4, "travel_month": "September"}, "preferences": {}}}'
```

From browser:

```text
http://13.201.32.120:8000/health
```

## EC2 Docker Commands

Status:

```bash
docker compose ps
```

FastAPI logs:

```bash
docker compose logs --tail=100 api
```

Rebuild only FastAPI:

```bash
docker compose up -d --build api
```

Start all services:

```bash
docker compose up -d --build
```

Stop all services without deleting data:

```bash
docker compose down
```

Do not run unless intentionally deleting n8n/Postgres data:

```bash
docker compose down -v
```

## FastAPI Rollback

Rollback using Git:

```bash
git revert BAD_COMMIT_SHA
git push
```

Then deploy on EC2:

```bash
cd ~/TravelWithMe
git pull origin master
docker compose up -d --build api
```

Emergency rollback on EC2:

```bash
cd ~/TravelWithMe
git log --oneline
git checkout GOOD_COMMIT_SHA
docker compose up -d --build api
```

Return to branch later:

```bash
git checkout master
git pull origin master
docker compose up -d --build api
```
