# Prompt changelog

Scout and Meridian are released independently. Add a separate entry whenever a prompt's behavioral instructions change.

## Scout 1.4.0 — 2026-07-16

- Preserved extracted traveler values verbatim under semantic keys while keeping qualifiers, relationships, budget boundaries, route distinctions, seasonal relevance, and trip shape intact.
- Kept Scout limited to extraction, initial routing, and complete general advice while Meridian owns destination and circuit recommendation work.
- Required answer-first advice to address every material ask with a practical verdict, useful guidance beyond query repetition, relevant pacing, trade-offs, and qualified time-sensitive guidance.

## Scout 1.2.0 — 2026-07-16

- Clarified that Scout owns entry routing and advice only until UI performs a specialist handoff.
- Limited null-message resume behavior to Scout-owned context; active specialist continuations bypass Scout.

## Meridian 1.1.0 — 2026-07-16

- Added the canonical prior-advice and traveler-message handoff inputs delivered by TWM-38.
- Made persisted `conversation_context.awaiting` authoritative for direct clarification answers and refinement turns.
- Defined continuing versus terminal outcomes and aligned examples with the canonical response cleanup.

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
