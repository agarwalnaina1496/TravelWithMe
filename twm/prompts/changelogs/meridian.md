# Meridian prompt changelog

## Meridian 1.6.0 — 2026-07-21

- Uses the Backend-supplied JSON Schema as the single structural output contract instead of duplicating hand-written JSON examples in the prompt.
- Requires one complete JSON object while retaining the existing recommendation and identity contract.

## Meridian 1.5.0 — 2026-07-18

- Resolves short conversational replies against active matching context and the awaited clarification.
- Preserves matching continuity without inventing traveler facts or storing conversational glue.
- Acknowledges farewells naturally while retaining an unfinished clarification for a later return.

## Meridian 1.4.0 — 2026-07-18

- Treated messages, TripState, prior outputs, recommendations, and retrieved content as untrusted data rather than executable instructions.
- Protected matching ownership, schemas, tools, hidden instructions, and traveler state while keeping legitimate destination matching available.

## Meridian 1.3.0 — 2026-07-17

- Replaced fixed recommendation sections with traveler ask mapped criteria shared by every option.
- Required one criterion evaluation per option with concise conclusions, supporting details, and criterion specific trade-offs.
- Preserved hard requirements, traveler qualifiers, route feasibility, cost boundaries, and time-sensitive uncertainty while keeping Planner-owned itinerary content out of recommendations.

## Meridian 1.2.0 — 2026-07-16

- Made Meridian the sole owner of destination and circuit recommendations, comparisons, ranking, narrowing, readiness, and refinement.
- Required Meridian to address the current ask before one material clarification and to recommend after the answer when ready.
- Made `why_ranked_here` the traveler-specific **Why this works for you** explanation, with every mismatch, uncertainty, cost, and allowed trade-off disclosed separately.
- Consolidated hard requirements, preferences, budget boundaries, considered choices, practical guidance, and qualification into one reasoning flow.
- Preserved matcher continuity, status contracts, and circuit feasibility rules without concrete prompt examples or duplicated clarification rules.

## Meridian 1.1.0 — 2026-07-16

- Added the canonical prior-advice and traveler-message handoff inputs delivered by TWM-38.
- Made persisted `conversation_context.awaiting` authoritative for direct clarification answers and refinement turns.
- Defined continuing versus terminal outcomes and aligned examples with the canonical response cleanup.

## Meridian 1.0.0 — 2026-07-12

- Established the first file-based Meridian prompt release.
- Captured the existing clarification, recommendation, failure, and ranking behavior without intentionally changing it.
