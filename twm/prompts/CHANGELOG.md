# Prompt changelog

Scout and Meridian are released independently. Add a separate entry whenever a prompt's behavioral instructions change.

## Meridian 1.2.0 — 2026-07-16

- Made Meridian the sole owner of destination and circuit recommendations, comparisons, ranking, narrowing, readiness, and refinement.
- Consolidated hard requirements, preferences, budget boundaries, considered choices, qualification, and traveler-specific trade-offs into one reasoning flow.
- Preserved matcher continuity, status contracts, and circuit feasibility rules without concrete prompt examples or duplicated clarification rules.

## Scout 1.3.0 — 2026-07-16

- Preserved material traveler context, comparison goals, uncertainty qualifiers, destination categories, route distinctions, and multi-stop intent without inventing adjacent facts.
- Made direct advice answer-first, traveler-complete, practical, and limited to one decision-changing clarification when needed.
- Added qualified seasonal and safety guidance with near-departure checks and disruption buffers when live verification is unavailable.

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
