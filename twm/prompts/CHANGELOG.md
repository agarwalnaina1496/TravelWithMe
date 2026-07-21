# Prompt changelog

Scout and Meridian are released independently. Add a separate entry whenever a prompt's behavioral instructions change.

## Meridian 1.6.0 — 2026-07-21

- Uses the Backend-supplied JSON Schema as the single structural output contract instead of duplicating hand-written JSON examples in the prompt.
- Requires one complete JSON object while retaining the existing recommendation and identity contract.

## Scout 1.7.0 — 2026-07-18

- Treats brief conversational turns as valid conversation rather than adversarial or clearly off topic.
- Keeps conversational glue out of traveler context unless it carries a material travel input or decision.

## Meridian 1.5.0 — 2026-07-18

- Resolves short conversational replies against active matching context and the awaited clarification.
- Preserves matching continuity without inventing traveler facts or storing conversational glue.
- Acknowledges farewells naturally while retaining an unfinished clarification for a later return.

## Scout 1.6.0 — 2026-07-18

- Added instruction hierarchy, prompt secrecy, untrusted-data handling, and a concise travel-only response for clearly off-topic turns.
- Kept mixed travel content useful while preventing injection, role, tool, prompt, and unrelated text from entering traveler context.

## Meridian 1.4.0 — 2026-07-18

- Treated messages, TripState, prior outputs, recommendations, and retrieved content as untrusted data rather than executable instructions.
- Protected matching ownership, schemas, tools, hidden instructions, and traveler state while keeping legitimate destination matching available.

## Meridian 1.3.0 — 2026-07-17

- Replaced fixed recommendation sections with traveler ask mapped criteria shared by every option.
- Required one criterion evaluation per option with concise conclusions, supporting details, and criterion specific trade-offs.
- Preserved hard requirements, traveler qualifiers, route feasibility, cost boundaries, and time-sensitive uncertainty while keeping Planner-owned itinerary content out of recommendations.

## Scout 1.5.0 — 2026-07-17

- Made complete advice end naturally after the useful guidance and limited follow-up questions to missing details that materially change Scout-owned advice.

## Meridian 1.2.0 — 2026-07-16

- Made Meridian the sole owner of destination and circuit recommendations, comparisons, ranking, narrowing, readiness, and refinement.
- Required Meridian to address the current ask before one material clarification and to recommend after the answer when ready.
- Made `why_ranked_here` the traveler-specific **Why this works for you** explanation, with every mismatch, uncertainty, cost, and allowed trade-off disclosed separately.
- Consolidated hard requirements, preferences, budget boundaries, considered choices, practical guidance, and qualification into one reasoning flow.
- Preserved matcher continuity, status contracts, and circuit feasibility rules without concrete prompt examples or duplicated clarification rules.

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
