# Prompt changelog

Scout and Meridian are released independently. Add a separate entry whenever a prompt's behavioral instructions change.

## Meridian 1.1.0 — 2026-07-16

- Added message-aware matching turns and prior-advice handoff context.
- Clarified that Meridian owns clarification and refinement after handoff and returns only agent-owned deltas.
- Replaced legacy response fields with the canonical optional constraint-adjustment field.

## Scout 1.1.0 — 2026-07-13

- Restored Given & Extract so reusable traveler signals are stored directly under `trip_context`.
- Clarified that verbatim preservation applies to useful extracted signals, not wholesale copies of the user's query.
- Removed model-generated advisor-memory duplication; the application owns deterministic persistence of visible replies.

## Scout 1.0.0 — 2026-07-12

- Established the first file-based Scout prompt release.
- Captured the existing extraction, routing, response, CTA, and resume behavior without intentionally changing it.

## Meridian 1.0.0 — 2026-07-12

- Established the first file-based Meridian prompt release.
- Captured the existing clarification, recommendation, failure, and ranking behavior without intentionally changing it.
