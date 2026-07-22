# Backend Telemetry

The Backend emits provider-neutral, one-line JSON to stdout. Deployment configuration may route that stream to Axiom today or another destination later; application code does not import a vendor SDK or use vendor-owned field names.

## Event envelope

Every event uses schema version `1.0` and contains `schema_version`, `timestamp`, `level`, `environment`, `service`, `source`, a concise human-readable `message`, application-owned `event`, and required `request_id`. Valid caller-provided `trip_id` and `turn_id` are optional. Sanitized `fields`, `payload_metadata`, `response_metadata`, `payload`, or `response` may be present.

Scout and Meridian emit readable checkpoints for HTTP receipt/response, validated input, agent invocation, raw adapter response, validation failure, repair, validated output, normalized public response, and failures. `attempt=1` identifies the initial invocation and `attempt=2` the single bounded repair invocation.

## Correlation contract

Scout and Meridian accept optional `X-TWM-Request-ID`, `X-TWM-Trip-ID`, and `X-TWM-Turn-ID` headers. Identifiers must start with an ASCII letter or digit, may then contain letters, digits, `.`, `_`, `:`, or `-`, and may contain at most 128 characters.

A valid value is echoed in the response. The Backend generates a UUID when the request ID is absent or invalid. Missing or invalid trip and turn IDs remain unset; the Backend never fabricates cross-turn identity. These values are diagnostic metadata, not authentication or authorization.

Context is held in an async-safe request-local variable and reset after the response, so concurrent requests do not share correlation data.

## Configuration

FastAPI reads these property-file values or equivalent environment variables:

```properties
TELEMETRY_ENABLED=true
TELEMETRY_PAYLOAD_MODE=metadata
TELEMETRY_MAX_FIELD_SIZE=16384
```

The common configuration disables telemetry, while the production overlay enables `full` diagnostic content for the approved Scout/Meridian call sites. Payload mode may be `off`, `metadata`, or `full`. `off` omits payload and response information, `metadata` records only type and serialized byte size, and `full` records sanitized, bounded content supplied by an approved call site.

All values pass through key-based secret redaction and string-size limits before reaching a sink. Authorization data, cookies, passwords, secrets, credential tokens, API keys, database or connection URLs, and webhook URLs are redacted. Non-secret usage metrics such as `input_tokens` and `output_tokens` remain queryable. Sink, serialization, and unsupported-value failures are fail-open and cannot change an API response.

Production diagnostic logs may contain traveler-provided trip context and model output after these controls. Access the Axiom dataset as production diagnostic data, keep queries narrowly scoped, and use `metadata` or `off` for immediate content containment.

## Extension boundary

Conversation call sites use `TelemetryLogger.debug/info/warning/error/critical` with a readable message, an application event name, and structured keyword fields. They do not construct envelopes or depend on a sink. `TelemetryLogger.event` remains an internal compatibility entry point. New destinations implement the small `TelemetrySink` delivery and shutdown protocol. Tests use `InMemorySink`. This boundary can later move to a separately versioned library while preserving application call sites and the envelope contract.

```python
telemetry.info(
    "Calling Scout",
    event="be.agent.invocation.started",
    agent="scout",
    engine="n8n",
    attempt=1,
    payload=sanitized_engine_input,
)
```

Production keeps stdout and may add standard OTLP/HTTP delivery when the production logs endpoint is configured. See [Production Axiom log delivery](AXIOM_LOG_DELIVERY.md) for sink selection, Render secrets, verification queries, credential rotation, usage checks, and provider-independent rollback.

## Rollback

Set `TELEMETRY_ENABLED=false` and redeploy for immediate shutdown. Correlation response headers do not change response bodies or agent behavior. Reverting the package and middleware requires no TripState, prompt, n8n, or public response-body migration.
