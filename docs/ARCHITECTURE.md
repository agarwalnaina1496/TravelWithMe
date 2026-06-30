# TWM Architecture

This document describes the general architecture. It is intentionally platform-agnostic. It has a small set of stable contracts and several swappable implementation choices.

## Design Philosophy

TWM features are independent, stateless product surfaces.

Trip Matcher and Trip Planner do not need to know about each other. They do not call each other, and there is no product-level orchestrator that enforces a sequence between them.

The user decides how far they go:

```text
- they can match and leave
- they can plan without ever matching
- they can go back and forth freely while status is free
- sequencing lives in the UI and the user's intent, not in feature coupling
```

What connects the product is shared state: [TripState](TRIP_STATE.md).

Each feature:

```text
1. reads relevant TripState sections when opened
2. does its job
3. writes back only the fields it owns or produces
```

The user experience feels connected because TripState travels across phases.

```text
        TripState
   localStorage -> DB
       ^     ^      ^
       |     |      |
  Matcher Planner Future features
```

This is different from the internal workflow/agent engine used inside a feature. A feature may use n8n, LangGraph, custom orchestration, or another engine internally, but features should remain independent from each other.

## Fixed Parts

These are the parts that should remain stable as the system evolves.

### 1. APIs

The UI should only need the backend base URL, not n8n or any other workflow engine.

Endpoint shape:

Examples:
```text
{base_url}/scout
{base_url}/meridian
```

**Trip Matcher**

[Trip Matcher API contracts](trip-matcher/API_CONTRACTS.md).

**Trip Planner**

PLACEHOLDER

### 2. TripState

[TripState](TRIP_STATE.md) is the shared object that connects phases.

Current storage:

```text
localStorage
```

Future storage:

```text
database
```

The storage layer can change without changing the core state model.

### 3. KB Schema

PLACEHOLDER

## Feature Boundaries

### Trip Matcher

Trip Matcher helps the user find the right destination.

Details live in [Trip Matcher](trip-matcher/README.md), [Scout](trip-matcher/SCOUT.md), and [Meridian](trip-matcher/MERIDIAN.md).

Reads:

```text
trip_context.required_inputs
trip_context.preferences
matcher_state
```

Scout returns deltas for:

```text
trip_context.required_inputs
trip_context.preferences
matcher_state.recommendation_intent
matcher_state.conversation_context
```

Meridian returns recommendation output for:

```text
matcher_state.recommendations
```

The UI owns deterministic matcher state writes:

```text
stage
trip_context.selected_option
matcher_state.rejected_options
```

### Trip Planner

Trip Planner helps the user plan the trip once a destination is known.

Reads:

```text
trip_context
```

Writes:

```text
planner_state
stage
```

Trip Planner does not read or write `matcher_state`.

Trip Planner should work whether or not Trip Matcher was used. If required planning context is missing, Planner asks the user and writes the answer to TripState.

### Future Features

Future features follow the same pattern:

```text
read relevant TripState sections
do their job
write back their owned output
```

## Swappable Parts

These can change without changing the whole product.

### Hosting Platform

Current:

```text
FastAPI on Render
n8n on AWS EC2
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

FastAPI talks to the current implementation through a stable `AgentEngine` interface:

```text
AgentEngine.scout(trip_state, message)
AgentEngine.meridian(trip_context)
```

The selected implementation is controlled by:

```env
agent_engine=n8n
```

Routes and UI contracts should not change when replacing n8n with LangGraph or custom Python orchestration. A new engine should implement the same `AgentEngine` methods and preserve the same `/scout` and `/meridian` response contracts.

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
Backend API -> hosted on Render

EC2
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

Current FastAPI Render details are in:

[Render FastAPI deployment](RENDER_FASTAPI.md)

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
          -> AgentEngine
              -> n8n today
              -> LangGraph / custom Python later
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
