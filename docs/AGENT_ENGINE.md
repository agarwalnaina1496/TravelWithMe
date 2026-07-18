# Agent Engine Selection

This runbook owns the Backend procedure for selecting n8n or LangGraph for both Scout and Meridian.

The public API remains engine-neutral:

```text
POST /scout
POST /meridian
```

The selected engine is read when FastAPI starts. Changing it requires a restart or redeploy. There is no per-request switch, automatic fallback, or shadow execution.

## Code Layout

```text
twm/services/
  agent_engine/
    contracts.py              engine-neutral protocol and AgentExecution
    factory.py                configuration-driven engine selection
    n8n.py                    n8n webhook adapter
    langgraph.py              LangGraph engine adapter
  langgraph/
    runtime.py                provider configuration and graph compilation
    state.py                  shared graph input, working state, and output
    nodes.py                  reusable input, invocation, and parser template nodes
    scout/
      models.py               Scout structured model output
      nodes.py                Scout invocation and output-parser nodes
      graph.py                Scout graph assembly and edges
    meridian/
      models.py               Meridian structured model output
      nodes.py                Meridian invocation and output-parser nodes
      graph.py                Meridian graph assembly and edges
```

Both graphs follow an explicit flow:

```text
START -> prepare_input -> invoke_<agent> -> parse_<agent>_output -> END
```

FastAPI owns the HTTP receive/respond boundary. LangGraph owns agent preparation, invocation, and structured-output parsing.

## Supported Values

```properties
AGENT_ENGINE=langgraph
AGENT_ENGINE=n8n
```

The committed and Render defaults are `n8n`. LangGraph is available only through an explicit manual configuration switch.

## LangGraph Requirements

LangGraph runs statelessly inside FastAPI and uses the Backend-owned prompt releases and response normalization.

Required secret:

```properties
GROQ_API_KEY=<secret>
```

Default non-secret settings:

```properties
LANGGRAPH_MODEL=openai/gpt-oss-120b
LANGGRAPH_TEMPERATURE=0.7
LANGGRAPH_TIMEOUT_SECONDS=60
```

Store `GROQ_API_KEY` only in the deployment platform or local secret environment. Do not add it to property files, `render.yaml`, logs, prompts, graph state, tests, or responses.

## n8n Requirements

The selected Backend environment must provide both webhook URLs:

```properties
N8N_SCOUT_WEBHOOK_URL=https://<n8n-host>/webhook/scout
N8N_MERIDIAN_WEBHOOK_URL=https://<n8n-host>/webhook/meridian
```

Both live workflows must be active. The versioned `n8n/*.json` files are backups; editing them does not update the live workflows. See [Self-hosted n8n](SELF_HOSTED_N8N.md).

Production webhook URLs must be HTTPS and each live Webhook node must require the server-only `X-TWM-Webhook-Token` Header Auth credential. Configure the matching `N8N_WEBHOOK_TOKEN` secret on FastAPI. See [Backend security boundaries](SECURITY_BOUNDARIES.md).

## Switch On Render

### Select LangGraph

1. Open the `travelwithme-api` service in Render.
2. Set `AGENT_ENGINE=langgraph`.
3. Set `GROQ_API_KEY` as a secret.
4. Confirm `LANGGRAPH_MODEL=openai/gpt-oss-120b` or another separately approved value.
5. Save the environment changes and redeploy/restart.
6. Confirm startup logs contain `Selected agent engine: langgraph` and do not contain credentials or traveler payloads.
7. Run the health and agent smoke checks below.

### Select n8n

1. Confirm the n8n service is healthy and both live workflows are active.
2. Confirm both webhook URL settings are present in the FastAPI environment.
3. Set `AGENT_ENGINE=n8n` in Render.
4. Save the environment change and redeploy/restart.
5. Confirm startup logs contain `Selected agent engine: n8n`.
6. Run the health and agent smoke checks below.

## Local Selection

Set the environment before starting FastAPI. PowerShell example:

```powershell
$env:AGENT_ENGINE = "n8n"
python -m uvicorn twm.main:app --host 0.0.0.0 --port 8000
```

For LangGraph, also provide `GROQ_API_KEY` through the local secret environment. Do not write the secret into the repository.

## Smoke Verification

Set `BASE_URL` to the deployed FastAPI origin.

Health:

```bash
curl --fail "$BASE_URL/health"
```

Expected:

```json
{"status":"ok"}
```

Scout:

```bash
curl --fail --request POST "$BASE_URL/scout" \
  --header "Content-Type: application/json" \
  --data '{
    "trip_state": {
      "stage": "new",
      "trip_context": {},
      "advisor_state": {}
    },
    "message": "Tell me what to consider for a mountain trip."
  }'
```

Verify `message`, `state_delta`, `intent`, and Backend-owned `agent_meta`. Engine-specific fields must not appear.

Meridian:

```bash
curl --fail --request POST "$BASE_URL/meridian" \
  --header "Content-Type: application/json" \
  --data '{
    "trip_state": {
      "trip_context": {"destination_scope": "mountains"},
      "advisor_state": {"conversation_context": {}},
      "matcher_state": {}
    },
    "message": "Help me narrow down mountain options."
  }'
```

Verify an approved `status`, `message`, `state_delta`, `options`, and Backend-owned `agent_meta`. Neither engine may write lifecycle stage, active-agent routing, selected option, or recommendation history.

## Manual Rollback To n8n

1. Confirm the n8n service and both live workflows are healthy.
2. Set `AGENT_ENGINE=n8n` on the FastAPI service.
3. Redeploy/restart FastAPI.
4. Confirm `Selected agent engine: n8n` in startup logs.
5. Run `/health`, `/scout`, and `/meridian` smoke checks.
6. Keep the UI deployment and stored TripState unchanged; neither requires migration.

Keep `AGENT_ENGINE=n8n` selected after rollback. Select LangGraph again only after it has been explicitly re-verified and approved, then redeploy, confirm the startup log, and repeat the smoke checks.

## Failure Behavior

FastAPI fails startup when the selected engine or its required configuration is invalid. Provider or graph execution failures do not invoke n8n automatically. Use the manual rollback procedure only after an operator decides to switch engines.
