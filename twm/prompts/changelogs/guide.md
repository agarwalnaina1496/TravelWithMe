# Guide prompt changelog

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
