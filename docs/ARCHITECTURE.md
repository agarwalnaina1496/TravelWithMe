# TWM Architecture

This document describes the general architecture. It is intentionally platform-agnostic. It has a small set of stable contracts and several swappable implementation choices.

## Fixed Parts

These are the parts that should remain stable as the system evolves.

### 1. APIs

The UI should only need the backend base URL, not n8n or any other workflow engine for that matter.

Base url + backend api

Examples:
```text
base url + /scout
base url + /meridian
```

**Trip Matcher**

[Trip Matcher API contracts](API_CONTRACTS.md).

**Trip Planner**

PLACEHOLDER

### 2. KB Schema

PLACEHOLDER

## Swappable Parts

These can change without changing the whole product.

### Hosting Platform

Current:

```text
AWS EC2
```

Swappable with:

```text
another VPS
AWS ECS
AWS Fargate
Render
Railway
Fly.io
Kubernetes
any Docker-capable backend server
```

The requirement is that the platform can run the backend services and expose the backend API to the UI.

### Backend API Framework

Current:

```text
FastAPI
```

Swappable with:

```text
Flask
Django
Node/Express
NestJS
Go
another HTTP API framework
```

The important part is preserving the API contract used by the UI.

### Workflow / Agent Engine

Current:

```text
n8n
```

Swappable with:

```text
LangGraph
custom Python orchestration
another workflow engine
```

Today n8n gives a visual workflow layer. Later LangGraph would make the backend lighter and more code-driven.

### LLM Provider

Current:

```text
Groq
```

Swappable with:

```text
Anthropic
Gemini
OpenRouter
local models
another LLM provider
```

The logic should not depend on LLM. GPT, Claude, Gemini, or another capable model should be able to execute the same instructions behind the same product.

### Frontend Hosting

Current:
```text
Vercel
```

Future UI hosting may be:

```text
Netlify
Cloudflare Pages
S3 + CloudFront
another frontend host
```

The only required configuration is the backend API base URL.

### Knowledge Base
Current:

```text
GitHub YAML files
  -> ingest.py
      -> Supabase free-tier Postgres
          -> Queries via SQL filters
```

Later:

```text
SQL filtering + semantic retrieval
```

For Trip Matcher, SQL filtering is enough for the current stage. Trip Planner may later need semantic retrieval in addition to structured filters.

## Current Deployment Shape

This is what exists today:

```text
UI -> hosted on Vercel
Backend -> hosted on EC2

EC2
  -> fast api container
  -> self hosted n8n
  -> postgres for n8n only

Knowledge base
GitHub YAML files
  -> ingest.py
      -> Supabase free-tier Postgres
          -> Queries via SQL filters

The KB database does not need to run on the backend server if Supabase is used.
```

Current EC2-specific details are in:

[EC2 setup](EC2_SETUP.md)

## Future Orchestration Layer scope

The orchestration layer is swappable. It can be:

```text
n8n
LangGraph
custom orchestration
another workflow/agent engine
```

The shape stays:

```text
UI
  -> Backend API
      -> FastAPI or another API framework
          -> orchestration layer
              -> KB queries
              -> LLM
```

If the orchestration layer is code-native and runs inside the API service, the backend server can become lighter:

```text
api container only
no separate orchestration container
no local Postgres container just for orchestration persistence
```

This can be more suitable for small instances, but it is a tradeoff. n8n gives a visual workflow layer; code-native orchestration gives tighter code control and fewer runtime services.
