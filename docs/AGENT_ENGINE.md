# Agent Engine Selection

This runbook owns the Backend procedure for selecting n8n or LangGraph for Scout and Meridian. The public `POST /scout` and `POST /meridian` contracts do not change with the selected engine.

The engine is selected when FastAPI starts. Changing it requires a restart or redeploy; there is no per-request switch, automatic fallback, or shadow execution.

## Common Execution Pipeline

FastAPI owns the agent behavior that must remain identical across engines:

```text
validate request
  -> load the Backend prompt release
  -> prepare the system and traveler messages
  -> invoke the selected thin adapter
  -> parse JSON and validate the Pydantic output contract
  -> regenerate once from the original request after invalid output
  -> normalize the public response
```

If the first completion is invalid, FastAPI makes exactly one compact regeneration invocation. The retry reuses the original trusted request plus sanitized failure categories and never copies the failed completion into the prompt. If the regenerated completion is still invalid, FastAPI returns a CORS-enabled `502`. Adapter timeouts return a CORS-enabled `504`. Parsing failures are infrastructure failures; they must never be represented as a successful Scout response or as Meridian `HARD_FAIL`.

The adapters return raw model text plus content-free provider telemetry when the selected engine exposes it. They do not parse or normalize model content:

- n8n: `Webhook -> Agent (+ configured model) -> Respond to Webhook`
- LangGraph: `START -> invoke_<agent> -> END`

## Code Layout

```text
twm/services/
  agent_engine/
    contracts.py   common engine and thin-adapter contracts
    service.py     shared preparation, parsing, validation, and repair
    settings.py    immutable selected-engine configuration
    factory.py     adapter selection and common-service assembly
    n8n.py         raw n8n webhook transport
    langgraph.py   raw LangGraph graph adapter
  langgraph/
    runtime.py     generic chat-model initialization and graph compilation
    state.py       messages-in/raw-output graph state
    nodes.py       reusable model invocation node
    scout/graph.py
    meridian/graph.py
```

## Supported Values

```properties
AGENT_ENGINE=n8n
AGENT_ENGINE=langgraph
```

The committed and Render default is `n8n`.

## n8n Private Contract

FastAPI sends this private payload to the selected agent webhook:

```json
{
  "system_prompt": "<Backend prompt plus output schema>",
  "user_prompt": "<framed traveler request>"
}
```

The workflow returns exactly:

```json
{"raw_output":"<unparsed model completion>"}
```

Both live workflows must be active. The versioned `n8n/*.json` files are backups; editing them does not update the live workflows. See [Self-hosted n8n](SELF_HOSTED_N8N.md).

## LangGraph Model Configuration

LangGraph uses LangChain's generic chat-model initializer. Application code does not depend directly on a provider-specific chat-model class. The current provider is Groq, backed by the installed provider integration package; a future provider can be selected through configuration and its corresponding integration dependency.

```properties
LANGGRAPH_MODEL_PROVIDER=groq
LANGGRAPH_MODEL=openai/gpt-oss-120b
LANGGRAPH_API_KEY=<secret>
LANGGRAPH_TEMPERATURE=0.7
LANGGRAPH_TIMEOUT_SECONDS=60
```

Keep `LANGGRAPH_API_KEY` only in the deployment platform or local secret environment. Do not add it to property files, logs, prompts, graph state, tests, or responses.

Only the selected engine's required settings are loaded. Missing LangGraph credentials do not block startup when n8n is selected.

## Switch On Render

To select LangGraph:

1. Set `AGENT_ENGINE=langgraph`.
2. Set the provider, model, and `LANGGRAPH_API_KEY` values above.
3. Redeploy or restart FastAPI.
4. Confirm the selected-engine startup log and run the smoke checks.

To select n8n:

1. Confirm both live n8n workflows use the current private raw-output contract and are active.
2. Set `AGENT_ENGINE=n8n`.
3. Redeploy or restart FastAPI and run the smoke checks.

## Smoke Verification

Check `GET /health`, then send representative requests to both `POST /scout` and `POST /meridian`. Verify that responses contain the existing public fields and Backend-owned `agent_meta`, and contain no engine-specific wrapper fields.

Neither engine may write UI-owned lifecycle stage, active-agent routing, selected option, or recommendation history.

## Rollback

1. Confirm the previous adapter and its configuration are healthy.
2. Restore the previous `AGENT_ENGINE` value.
3. Redeploy or restart FastAPI.
4. Re-run `/health`, `/scout`, and `/meridian` smoke checks.

No UI deployment or TripState migration is required because the public API contract remains unchanged.
