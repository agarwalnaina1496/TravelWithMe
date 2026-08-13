# Guide prompt changelog

## Guide 2.2.0 — 2026-08-14

- Renames the `duration` awaiting slug to `duration_days` so every
  `GuideAwaiting` value is identical to its `trip_context` key name with
  no exceptions (previously `duration` was the one field whose slug
  didn't match its trip_context key). Meridian's `awaiting` is renamed the
  same way in this release, and the UI's quick-reply lookup is updated to
  match.

## Guide 2.1.0 — 2026-08-14

- Expands the START/TRAVELER_MESSAGE `awaiting` gate from `duration` alone
  to five fixed trip_context inputs: `duration`, `origin_city`,
  `num_travelers`, `travel_dates`, `budget` (checked and asked for in that
  order, one at a time). `GuideAwaiting` grows from `Literal["duration"]`
  to a five-value enum. `travel_dates` and `budget` accept any verbatim
  form the traveler gives (a range, "flexible", a month, tentative dates,
  "don't know yet") — never coerced to a fixed type. `budget_unit` is
  intentionally not part of the fixed key set.
- Aligns Guide's shared `trip_context` fixed-key set with Scout's restored
  `origin_city`/`num_travelers`/`duration_days`/`travel_dates`/`budget`
  keys.

## Guide 2.0.0 — 2026-08-13

- Breaking contract change: Guide now returns `state_delta` (only the
  fields it is intentionally changing) instead of a full-state
  `guide_state` echo. Mirrors Scout/Meridian's delta+merge pattern —
  `duration_days`/`destinations`/`start_date`/`preferences`/`exclusions`
  move to the shared `trip_context` (deep-merged, `preferences`/
  `exclusions` union-merged across turns and specialists); `places`/
  `day_plan` stay Guide-owned under `planner_state`, replaced wholesale
  only when included in the delta.
- Removes `explicit_changes`, `phase`, `applied_changes`, and
  `pending_clarification` from the contract. Backend derives readiness
  from what's actually present in `planner_state` instead of trusting a
  self-reported phase, and validates day-plan/duration consistency
  against the merged state after every turn instead of requiring the
  model to correctly echo back everything it isn't touching.
- `pending_clarification` is replaced by
  `planner_state.conversation_context.awaiting` (a fixed slug, currently
  only `"duration"`) — same pattern as Meridian's `awaiting`, so the UI
  can drive it with the same quick-reply mechanism.
- Fixes a real bug this surfaced: a full-state echo occasionally dropped
  or altered `places` on a turn that was only answering the duration
  question, which Backend's old undeclared-change guard caught as a 422
  instead of silently corrupting the traveler's places list. Under the
  delta contract, Guide has no reason to touch `places` on such a turn
  and so structurally cannot drift it — the failure mode is not just
  caught, it no longer exists for fields left out of the delta.

## Guide 1.5.0 — 2026-08-13

- APPROVE_PLAN no longer reaches Guide at all — Backend applies the
  preserve-day-plan-and-freeze transition deterministically
  (`planner_commands.py`), since the LLM was only ever being asked to
  confirm state Backend already validated unchanged. Saves one LLM call
  per completed Guide session with no behavior change.

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
