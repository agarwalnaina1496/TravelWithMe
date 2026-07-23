# Production Axiom Log Delivery

This runbook owns production Backend delivery to Axiom through the standard OpenTelemetry OTLP/HTTP logs protocol. Application events remain provider-neutral and continue to be written as one-line JSON to stdout.

```text
TelemetryLogger
  -> CompositeSink
       -> JsonStdoutSink -> Render service logs
       -> OtlpHttpSink   -> configured OTLP/HTTP destination
                              -> Axiom twm-production
```

The OTLP sink is constructed only when `ENVIRONMENT=prod` and `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` is non-empty. Non-production processes never construct remote delivery, even if an OTLP endpoint is accidentally present. Removing the endpoint restores stdout-only delivery without changing event call sites.

## Production resources

| Resource | Value |
| --- | --- |
| Axiom dataset | `twm-production` |
| Dataset kind | Events |
| Dataset retention | 30 days |
| Render service | `travelwithme-api` |
| Transport | OTLP/HTTP with protobuf |

The Backend uses the OpenTelemetry SDK and exporter; it does not use an Axiom SDK. Endpoint and authentication headers follow standard OTLP environment variables. Another OTLP-compatible provider can replace Axiom without changing application event call sites or the telemetry envelope.

## Personal-plan operating limits

The July 2026 Axiom Personal limits relevant to this deployment are 25 GB always-free storage, 500 GB monthly loading, 10 GB-hours monthly query compute, 30-day retention, two datasets, 256 fields per dataset, one user, and three monitors. Check current limits before repeating this setup because plans can change. Review **Settings > Usage** weekly during active development and before enabling higher-volume event categories.

Sources: [Axiom limits](https://axiom.co/docs/reference/limits), [Axiom usage](https://axiom.co/docs/reference/usage-billing), and [Axiom OpenTelemetry](https://axiom.co/docs/send-data/opentelemetry).

## Configure Axiom

1. Create an Events dataset named `twm-production` with the Personal plan's 30-day retention.
2. Create an API token dedicated to Render production log ingestion.
3. Grant only ingest permission for `twm-production`; do not grant query, admin, or access to other datasets.
4. Copy the token directly into Render's secret environment configuration. Never place it in source control, tickets, screenshots, chat, or log events.

Axiom accepts OTLP logs at `https://api.axiom.co/v1/logs` for the default US deployment. If the organization uses another edge deployment, use that deployment's base domain followed by `/v1/logs`.

## Configure Render production

Set these variables on the production Backend service:

```properties
ENVIRONMENT=prod
OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://api.axiom.co/v1/logs
OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_LOGS_HEADERS=Authorization=Bearer <ingest-only-token>,x-axiom-dataset=twm-production
```

Store `OTEL_EXPORTER_OTLP_LOGS_HEADERS` as a secret. The endpoint may also remain an uncommitted Render value. `render.yaml` declares the configuration slots but contains neither value. Do not configure Render's workspace Log Stream for this path: Hobby workspace streaming would also ingest dev-service logs.

The exporter batches records in-process. The FastAPI lifespan shuts the provider down so pending records are flushed during an orderly stop. Remote failures remain isolated from stdout and traveler-facing API behavior.

## Field representation

Each OTLP record carries:

- the concise human-readable `message` as the primary log body shown in Axiom;
- standard OTLP severity derived from `level`;
- the application timestamp as the log timestamp;
- the sanitized structured envelope, except for the duplicate `message`, as flattened query attributes such as `request_id`, `trip_id`, `turn_id`, `event`, `fields.agent`, `fields.attempt`, `payload.*`, and `response.*`;
- `service.name=travelwithme-backend` as the OpenTelemetry resource.

Stdout remains one-line JSON and contains both `message` and the structured fields. Structured data must be queryable in Axiom; an escaped JSON-only message does not satisfy verification.

## Configure the readable Stream view

This is a one-time Axiom explorer setup, not a query investigators must rewrite:

1. Open **Stream** and select `twm-production`.
2. Open the Stream view settings.
3. Turn off **Show the raw event details** so the native OTLP log body is the primary row text.
4. Turn on **Highlight severity** and **Wrap lines**.
5. Keep structured attributes available in the event details panel for filtering and expansion.

The supported production explorer must show messages such as `Scout called via n8n with message "..."` and `Scout invocation via n8n failed. Detail - ConnectError: ...` without adding an APL `project` clause or selecting `attributes.message`. The OTLP body is the single source of the primary message; `attributes.message` is intentionally not emitted.

Failure rows also expose a stable taxonomy through structured fields:

| Field | Purpose | Example |
| --- | --- | --- |
| `component` | Boundary that failed | `fastapi`, `n8n`, `langgraph` |
| `operation` | Operation being performed | `scout.invoke` |
| `failure_stage` | Stage within the boundary | `upstream_connection`, `response_contract` |
| `error_type` | Actionable normalized or underlying type | `ConnectError` |
| `error_detail` | Sanitized and bounded diagnostic detail | `all connection attempts failed` |
| `upstream_status_code` | n8n HTTP status when available | `503` |

## Production smoke test

After the Backend revision is deployed, send a validation request with a unique request ID:

```powershell
$smokeRequestId = "axiom-smoke-<UTC timestamp>"
curl.exe -X POST "https://travelwithme-zf9f.onrender.com/scout" `
  -H "Content-Type: application/json" `
  -H "X-TWM-Request-ID: $smokeRequestId" `
  --data "{}"
```

A `422` response is expected and proves middleware and FastAPI validation coverage without invoking an agent. In Axiom Stream, confirm the primary row says `FastAPI rejected Scout request. Detail - RequestValidationError: ...` and the event details include the same request ID, `component=fastapi`, and `failure_stage=request_validation`.

Then submit one valid Scout turn and one valid Meridian turn from the production UI with a unique trip ID. Verify the ordered messages include `Received ... request`, `... called via <engine> with message ...`, `... response received`, `... response validated`, and `Returning ... response`. Trigger one safe failure and confirm its primary row identifies FastAPI, n8n, or LangGraph plus the operation, error type, and sanitized detail. Inspect the records and confirm severity, resource service name, readable body, correlation fields, engine, attempt, sanitized payload, and sanitized response are independently queryable.

## APL query templates

Save the following queries after the smoke test. Axiom may expose OpenTelemetry attributes with an `attributes.` prefix; use the exact field path shown by the first ingested event.

### TWM - Request timeline

```apl
['twm-production']
| where attributes.request_id == 'replace-request-id'
| order by _time asc
```

### TWM - Recent Backend errors

```apl
['twm-production']
| where resource.service.name == 'travelwithme-backend'
| where severityText == 'ERROR' or severityText == 'CRITICAL'
| order by _time desc
| limit 100
```

### TWM - Agent attempts

```apl
['twm-production']
| where attributes.event startswith 'be.agent.'
| project _time, body, attributes.request_id, attributes.trip_id, attributes.fields.agent, attributes.fields.engine, attributes.fields.attempt, attributes.event
| order by _time desc
| limit 200
```

### TWM - Payload volume by hour

```apl
['twm-production']
| where resource.service.name == 'travelwithme-backend'
| summarize events=count(), payload_bytes=sum(attributes.payload_metadata.size_bytes) by _time=bin(_time, 1h)
| order by _time desc
```

Keep query time ranges narrow to conserve Personal-plan query compute.

## Token rotation

1. Create a replacement ingest-only Axiom token scoped to `twm-production`.
2. Replace only the bearer value inside `OTEL_EXPORTER_OTLP_LOGS_HEADERS` on Render.
3. Redeploy, run the smoke test, and confirm new events arrive.
4. Revoke the old token.

If a token is exposed, rotate it immediately and never paste the compromised value into an incident ticket.

## Disable, replace, and roll back

For immediate delivery rollback, remove `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` and redeploy. FastAPI continues writing structured stdout and serving requests. Revoke the Axiom token if compromise is suspected.

To replace Axiom, configure the endpoint, protocol, and headers required by another OTLP/HTTP-compatible destination, deploy, and repeat the correlation smoke test. If a future destination requires transformation or routing beyond standard OTLP configuration, add a collector as separately approved architecture work.

## Troubleshooting

- No remote events: confirm `ENVIRONMENT=prod`, the logs endpoint includes `/v1/logs`, protocol is `http/protobuf`, headers contain both authorization and dataset routing, and the token has ingest permission.
- Stdout exists but Axiom does not: inspect Render service logs for exporter transport errors; API behavior should remain unaffected.
- Fields are not queryable: inspect the event's body and attributes in Axiom and update query paths only after confirming the structured values exist.
- Duplicate events: confirm only one OTLP sink is configured and Render workspace Log Streams remain disabled.
- Missing historical events: Personal retention is capped at 30 days; archival is outside this story.
- High usage: narrow query windows and review **Settings > Usage** for ingest, query compute, and field growth.
