# Backend Telemetry

The Backend emits provider-neutral, one-line JSON to stdout. Deployment configuration may route that stream to Axiom today or another destination later; application code does not import a vendor SDK or use vendor-owned field names.

## Event envelope

Every event uses schema version `1.0` and contains `schema_version`, `timestamp`, `level`, `environment`, `service`, `source`, application-owned `event`, and required `request_id`. Valid caller-provided `trip_id` and `turn_id` are optional. Sanitized `fields`, `payload_metadata`, or `payload` may be present.

The foundation events are `be.http.request.received`, `be.http.response.sent`, and `be.http.request.failed`. Detailed Scout and Meridian pipeline events belong to the separate instrumentation increment.

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

The common configuration disables telemetry, while the production overlay enables metadata-only lifecycle events. Payload mode may be `off`, `metadata`, or `full`. `off` omits payload information, `metadata` records only type and serialized byte size, and `full` permits sanitized content supplied by an approved call site. Changing the setting does not make the foundation collect request bodies, prompts, TripState, or provider responses by itself.

All values pass through key-based secret redaction and string-size limits before reaching a sink. Authorization data, cookies, passwords, secrets, tokens, API keys, database or connection URLs, and webhook URLs are redacted. Sink, serialization, and unsupported-value failures are fail-open and cannot change an API response.

## Extension boundary

Business call sites depend on `TelemetryLogger.event`, not `JsonStdoutSink`. New destinations implement the small `TelemetrySink.emit` protocol. Tests use `InMemorySink`. This boundary can later move to a separately versioned library while preserving application call sites and the envelope contract.

## Rollback

Set `TELEMETRY_ENABLED=false` and redeploy for immediate shutdown. Correlation response headers do not change response bodies or agent behavior. Reverting the package and middleware requires no TripState, prompt, n8n, or public response-body migration.
