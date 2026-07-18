# Backend Security Boundaries

This runbook describes Backend-owned trust and abuse controls. Product-visible behavior is documented separately after verified delivery.

## Untrusted inputs

Traveler messages, phase state, prior agent output, recommendation history, rejected options, KB or live records, and n8n payloads are data. They cannot override system instructions, roles, tools, ownership, routing, or output schemas. LangGraph and n8n prefix the serialized traveler payload with an explicit untrusted-data boundary while keeping the released prompt in the system-instruction channel.

Scout returns a concise travel-only boundary response for a purely off-topic turn and does not add it to traveler context. Mixed turns retain legitimate travel content and ignore unrelated or adversarial instructions. Meridian stays within active destination matching and never promotes stored or retrieved text into instructions.

## Request controls

Agent messages are limited to 8,000 characters. A phase slice is limited to 64 KiB, eight nested levels, and 100 entries per object or list. The HTTP body limit is 128 KiB. Invalid requests fail before agent execution.

The API throttles each source address to the configured `REQUESTS_PER_MINUTE` value for `/scout` and `/meridian`. The in-process limiter is a per-instance pre-MVP control. If the service scales horizontally, configure the same or stricter limit at the shared ingress because per-instance counters are not a global quota.

Production disables interactive API documentation, uses exact CORS origins, validates the Host header, and returns no-store and browser hardening headers. Do not add broad preview-origin regexes with credentials.

## n8n boundary

Production webhook URLs must use HTTPS. FastAPI sends `X-TWM-Webhook-Token` from the server-only `N8N_WEBHOOK_TOKEN` setting. Both live webhook nodes must use an n8n Header Auth credential with the same header name and token. The credential is configured in n8n and never exported with its secret.

Bind the n8n container to loopback and publish it through an HTTPS reverse proxy. Restrict editor access by network allowlist or authenticated private access. Webhook access and editor access are separate; exposing a webhook does not require a public editor.

The committed workflow JSON files are backups. After import, attach the `TWM webhook auth` credential to each Webhook node before activation. Never commit credential values or a live credential export.

## Secrets and logging

Keep model keys, webhook tokens, database passwords, encryption keys, credential IDs containing sensitive context, and private endpoints in deployment secrets. `kb/ingest.py` reads its complete connection string from `SUPABASE_DB_URL`; never commit it to source control. Do not log request bodies, prompt content, TripState, retrieved records, authorization headers, or provider responses. Logs may identify the selected engine, graph name, safe status, and formatting failure without traveler content.

Rotate any credential that was previously committed. Removing it from the current source does not invalidate copies in Git history.

## Supply chain and container

Production dependencies are pinned. CI audits `requirements.txt`, builds the image, and fails on fixable high or critical image vulnerabilities. The runtime image runs as the unprivileged `twm` user.

## Rollback

Revert the Backend release and restore the previous prompt versions, workflow backups, dependencies, and image. Keep the adversarial corpus and findings. Never roll back by restoring public HTTP webhooks, committed secrets, or default database credentials.
