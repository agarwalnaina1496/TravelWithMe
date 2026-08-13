# Guide prompt changelog

## Guide 1.4.0 — 2026-08-13

- START and APPROVE_PLACES now require asking for `duration_days` (via
  NEEDS_CLARIFICATION) before proposing a day plan, instead of deferring
  the check to APPROVE_PLACES only — Guide no longer jumps straight to a
  PLACES_DRAFT/day plan while duration is still unknown.
- TRAVELER_MESSAGE asks a single "anything else to add or change?" question
  once the last absolutely-necessary input (most commonly duration) is
  supplied, before the day plan is built — keeps input-gathering as a chat
  conversation rather than a silent jump to itinerary generation.

## Guide 1.3.0 — 2026-08-12

- Adds a required `pace` signal (`relaxed`/`balanced`/`packed`) to every day
  plan entry, and an optional `buffer_note` for a specific, meaningful gap
  worth naming. Judged from place count/effort/travel, never from cost,
  which Guide still does not own.
- Requires explaining a meaningful practical consequence of a traveler edit
  (e.g. a removed place opening up free time, a removed day tightening pace
  elsewhere) in the response `message`, in terms of time and pace rather
  than price, instead of a generic acknowledgment.

## Guide 1.2.0 — 2026-08-11

- Adds `outcome = "reopen_destination_discovery"` for TRAVELER_MESSAGE only,
  reserved for an explicit, unambiguous traveler request to abandon the
  current destination and return to destination discovery.
- Backend validates and executes the transition; Guide only proposes it and
  otherwise stays with `outcome = "continue"`, including for contextual
  questions, ordinary plan edits, and ambiguous language.

## Guide 1.1.0 — 2026-08-07

- Declares current-turn traveler-directed state changes through the structured
  `explicit_changes` contract.
- Lets Backend distinguish an intentional traveler override from accidental
  loss in Guide's full replacement state.

## Guide 1.0.0 — 2026-08-03

- Establishes the first Guide prompt release for places-first trip design.
- Preserves explicit traveler decisions while supporting place edits and a
  day-wise, place-only working plan.
- Keeps researched itinerary details within Atlas ownership.
