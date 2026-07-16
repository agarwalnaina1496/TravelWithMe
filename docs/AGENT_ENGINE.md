# Agent Engine Foundation

This document records the Backend runtime foundation for the planned global agent-engine setting.

## Current Release

The committed and deployed default remains:

```properties
AGENT_ENGINE=n8n
```

Scout and Meridian continue using the existing n8n webhooks. This foundation release does not provide production Scout or Meridian LangGraph execution and does not authorize selecting LangGraph in a deployed environment.

If `AGENT_ENGINE=langgraph` is selected in this foundation release, FastAPI fails startup with a clear message that the concrete agent implementation is not available. There is no silent fallback.

## Planned Global Contract

The concrete agent implementation will complete the manual startup/redeploy contract:

```properties
AGENT_ENGINE=n8n
AGENT_ENGINE=langgraph
```

One value will select the same engine for both Scout and Meridian. There will be no per-request switch, per-agent flag, automatic fallback, or shadow execution. Changing the value will require a service restart or redeploy.

## LangGraph Runtime Prerequisites

The reusable runtime foundation supports these non-secret settings:

```properties
LANGGRAPH_MODEL=openai/gpt-oss-120b
LANGGRAPH_TEMPERATURE=0.7
LANGGRAPH_TIMEOUT_SECONDS=60
```

Constructing the runtime also requires:

```properties
GROQ_API_KEY=<secret>
```

Store `GROQ_API_KEY` only in the deployment platform or local secret environment. Do not add it to property files, `render.yaml`, logs, prompts, graph state, tests, or responses.

Invalid model, timeout, temperature, or credential configuration fails clearly when the LangGraph runtime is constructed.

## n8n Requirements

The current Backend environment must continue providing both webhook URLs:

```properties
N8N_SCOUT_WEBHOOK_URL=https://<n8n-host>/webhook/scout
N8N_MERIDIAN_WEBHOOK_URL=https://<n8n-host>/webhook/meridian
```

Both live workflows must remain active. The versioned `n8n/*.json` files are backups; changing a Git export does not update the live workflow. See [Self-hosted n8n](SELF_HOSTED_N8N.md).

## Completion Work

The concrete agent implementation must add:

* production Scout and Meridian LangGraph graphs;
* shared behavioral parity tests;
* deployed smoke verification for both engines;
* the switch to LangGraph as the canonical and production default;
* re-testing `AGENT_ENGINE=n8n` as the manual rollback path;
* final copyable Render switch, smoke-test, and rollback steps.

Until those checks pass, keep `AGENT_ENGINE=n8n`.
