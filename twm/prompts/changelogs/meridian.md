# Meridian prompt changelog

## Meridian 1.11.0 — 2026-08-20

- Added a gate before recommending, structured to directly mirror Guide's
  existing six-input START gate (`twm/prompts/guide.md`): walk the five
  shared `trip_context` fields relevant to the current ask in order, then
  ask a sixth open "anything else you'd like to add?" question
  (`awaiting: "anything_else"`) and receive an answer before ever
  returning `SUCCESS`/`SOFT_FAIL`, even when every relevant field is
  already known. Fires at most once per trip; a terminal failure status
  may still be returned before the gate is answered when success is
  already impossible. Closes an edge case (TWM-189) where Meridian could
  recommend on a trip's very first turn, before any trip row exists to
  archive the recommendation against.

## Meridian 1.10.0 — 2026-08-14

- When `awaiting` names one of the five shared `trip_context` facts,
  Meridian now uses the exact trip_context key name as the slug
  (`origin_city`, `num_travelers`, `trip_duration`, `travel_dates`,
  `budget`) instead of an ad hoc synonym (previously e.g. `"origin"`).
  Matches Guide's `GuideAwaiting` enum, which is renamed the same way in
  this release (`"duration"` -> `"trip_duration"`), so every awaiting slug
  is now identical to its trip_context key with no exceptions, and the
  UI's quick-reply lookup keys off one shared name per fact.
- Rewrites several behavioral instructions from negative ("do not X") to
  positive framing where the rule has no specific failure mode to warn
  against. Kept the original explicit warning where it did (never replay a
  More like this reference unchanged, a hard budget boundary never gets
  quietly relaxed). Kept the Ownership Boundary's negative framing as-is:
  it describes what Meridian itself does not do, and rephrasing it would
  require naming what Scout/Planner do instead, which is their scope to
  state, not Meridian's. Safety/injection guardrails unchanged.

## Meridian 1.9.0 — 2026-08-11

- Required Circuit Feasibility to validate the return leg against any stated return-timing constraint (a fixed return date, a weekend-only window, or needing to be back by a specific day), not only outbound and inter-stop legs.
- Treated return-timing as its own criterion evaluated with the same route arithmetic as the rest of the circuit, surfacing MATCH, TRADEOFF, or MISMATCH rather than silently approving a route that realistically misses the stated constraint.

## Meridian 1.8.0 — 2026-08-11

- Defined `matcher_state.refinement` as a Backend-supplied, already-validated More like this signal carrying a MORE_LIKE_THIS type and a canonical single or circuit reference identity.
- Required the referenced option to act as a positive direction while preserving every existing traveler criterion and hard requirement.
- Required optional `refinement.instructions` to refine, not replace, known traveler context.
- Required a fresh ranked result rather than replaying or mutating the referenced option.

## Meridian 1.7.0 — 2026-08-11

- Required conversational interpretation of total versus per-person budget without a fixed form.
- Required a rough complete-trip affordability estimate for the full party, covering origin, traveler count, duration, round-trip access, stay, food, local movement, and named required activities where reasonably estimable.
- Let affordability and complete round-trip feasibility influence candidate generation, exclusion, and ranking, without hardcoding flight rejection or any transport-mode numeric threshold.
- Required circuits to account for the complete round trip, including outbound and return legs, and clarified that day-level return-timing constraints remain owned by circuit-feasibility validation elsewhere.
- Reaffirmed that hard budget boundaries cannot be silently relaxed and that missing cost estimates must be qualified or omitted rather than treated as zero.

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
