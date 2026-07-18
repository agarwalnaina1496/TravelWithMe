# EC2 Deployment Setup

This document records the EC2 setup that now hosts n8n and n8n's Postgres only.

FastAPI has moved to Render. See [Render FastAPI deployment](RENDER_FASTAPI.md).

For the platform-agnostic architecture, including fixed vs swappable parts and future KB/LangGraph direction, use:

[Architecture](ARCHITECTURE.md)

For n8n-specific setup, workflow updates, backups, and rollback, use:

[Self-Hosted n8n](SELF_HOSTED_N8N.md)

## What Is Deployed

```text
AWS EC2 Ubuntu server
  -> Docker Compose
      -> n8n container
      -> Postgres container for n8n only
```

The UI should call FastAPI on Render:

```text
https://<render-service-host>
```

n8n is used as the internal workflow/orchestration layer:

```text
https://n8n.example.com
```

Postgres is not currently the app knowledge base. It is only used by n8n to persist workflows, credentials, and execution data.

## EC2 Instance Details

```text
Provider: AWS EC2
AMI: Ubuntu Server 24.04 LTS (HVM), SSD Volume Type
Architecture: 64-bit x86 / amd64
Instance type: t3.micro
Storage: 20 GiB EBS
Public IPv4: <EC2_PUBLIC_IP>
User: ubuntu
Local PEM path: <secure-local-key-path>
```

`t3.micro` is okay for testing/MVP. For smoother n8n usage, `t3.small` is preferred.

## Security Group Rules

Required inbound rules keep n8n and SSH off the public internet:

```text
22    TCP  SSH      <ADMIN_IP>/32
80    TCP  HTTP     0.0.0.0/0
443   TCP  HTTPS    0.0.0.0/0
5678  TCP  n8n      no public rule
```

## Connect To EC2

From local Git Bash:

```bash
ssh -i <secure-local-key-path> ubuntu@<EC2_PUBLIC_IP>
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

## Config Files

Files under `twm/shared/properties/` are for FastAPI only. Since FastAPI now runs on Render, EC2 n8n should not use those property files.

Create `n8n.env` on EC2 from the template:

```bash
cd ~/TravelWithMe
cp n8n.env.example n8n.env
nano n8n.env
```

For n8n-specific values, encryption key, and public URL settings, see [Self-Hosted n8n](SELF_HOSTED_N8N.md).

Do not commit `n8n.env`.

## Start The EC2 n8n Stack

On EC2:

```bash
cd ~/TravelWithMe
docker compose up -d
docker compose ps
```

This starts `postgres` and `n8n`. It does not start FastAPI by default.

## Live URLs

From browser:

```text
FastAPI health:
https://<render-service-host>/health

n8n:
https://n8n.example.com
```

UI base API URL:

```text
https://<render-service-host>
```

UI should call:

```text
POST https://<render-service-host>/scout
POST https://<render-service-host>/meridian
```

## n8n Setup And Updates

n8n is running on the same EC2 instance, but n8n-specific setup and workflow operations live in [Self-Hosted n8n](SELF_HOSTED_N8N.md).

Use that doc for:

```text
- n8n EC2 setup
- workflow import/export
- workflow updates
- n8n backup
- n8n rollback
```

## FastAPI Update Flow

FastAPI deploys through Render now. Use [Render FastAPI deployment](RENDER_FASTAPI.md). FastAPI properties are loaded from `twm/shared/properties/properties.ini` and `twm/shared/properties/properties-{ENVIRONMENT}.ini`, following the `[APP]` property-file pattern.

On local machine:

```bash
git status
git add .
git commit -m "Update FastAPI"
git push
```

## Prompt Update Flow

Prompt files:

- [Scout system prompt](../twm/prompts/scout.md)
- [Meridian system prompt](../twm/prompts/meridian.md)

FastAPI loads these files locally and sends prompt text to the selected `AgentEngine`.

Since FastAPI runs on Render, prompt changes deploy through Render with the FastAPI service.

On local machine:

```bash
git add twm/prompts
git commit -m "Update prompts"
git push
```

## EC2 n8n Testing

Check n8n:

```bash
docker compose ps
docker compose logs --tail=100 n8n
```

FastAPI health is checked on Render:

```text
https://<render-service-host>/health
```

Scout and Meridian should be tested against Render:

```text
POST https://<render-service-host>/scout
POST https://<render-service-host>/meridian
```

## EC2 Docker Commands

Status:

```bash
docker compose ps
```

Start all services:

```bash
docker compose up -d
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

Then deploy through Render.

Emergency rollback:

```bash
cd ~/TravelWithMe
git log --oneline
git checkout GOOD_COMMIT_SHA
```

Return to branch later:

```bash
git checkout master
git pull origin master
```
