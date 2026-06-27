# Meridian

Meridian is the Trip Matcher decision engine.

It receives complete trip context, runs travel region matching, and returns ranked recommendations with reasoning, tradeoffs, and refinement hooks.

Meridian is stateless. It does not hold conversation history. It does not decide when to run. The traveler triggers generation from the UI after Scout sets recommendation intent.

## Responsibilities

```text
- validate required matching inputs
- read trip context and preferences
- generate travel region candidates from KB
- eliminate candidates that violate hard constraints
- check origin feasibility and transport viability
- score surviving candidates
- return a ranked shortlist with clear reasoning
- return failure states when constraints cannot produce good options
- provide refinement hooks for Scout
```

Scout handles conversation. Meridian handles matching. Planner handles execution.

---

## Inputs

Meridian receives `trip_context`.

For the API shape, see [Trip Matcher API contracts](API_CONTRACTS.md).

Required inputs:

```text
origin_city
budget
budget_unit
duration_nights
num_travelers
travel_month
```

Useful preference signals:

```text
trip_goal
travel_style
crowd_tolerance
weather_preference
group_type
budget_flexibility
explicit_exclusions
nuanced_preferences
```

`nuanced_preferences` preserves traveler language that does not fit cleanly into structured fields.

Examples:

```text
Wants to walk around and explore.
Prefers places that feel local, not built only for tourists.
Wants food to be easy to find without research.
Does not want a heavy itinerary.
```

---

## Matching Pipeline

Meridian's matching flow is:

```text
0. Parse inputs
1. Structured KB filtering
2. Origin feasibility
3. Goal fit evaluation
4. Scoring
5. Failure detection
```

### Step 0 — Parse Inputs

Parse and validate `trip_context`. Apply explicit traveler overrides.

```text
explicit traveler preference > system defaults > fallback heuristics
```

Examples:

```text
- "No flights" → mark as hard exclusion before Step 1
- "Happy to travel 20 hours" → loosen travel time threshold in Step 2
- "Budget is not an issue" → do not assume luxury unless traveler asks for it
```

Flag `MISSING_INPUTS` if required fields are absent.

---

### Step 1 — Structured KB Filtering

KB stores traveler-agnostic destination facts. Meridian queries the KB to eliminate regions that objectively do not fit.

```text
season mismatch       → travel_month in avoid_months → out
budget mismatch       → in_destination_tier incompatible with budget tier → out
hard exclusions       → destination flags conflict with explicit traveler exclusions
                        e.g. "no trekking" → trekking_required: true → out
                        e.g. "no beach" → beach: true → out
environment mismatch  → e.g. "lush greenery" → forest_cover: none → out
                        e.g. "prefer cool weather" → climate: tropical_humid in summer → out
constraint conflict   → e.g. connectivity: low → workation traveler → out
                        e.g. luxury_inventory_available: false → luxury traveler → out
```

This step is pure structured filtering. No traveler interpretation happens here.

---

### Step 2 — Origin Feasibility

After KB filtering, Meridian checks reachability and cost from origin using live data.

```text
- travel time from origin_city to candidate region (Maps API)
- transport cost from origin_city (live)
- total cost = travel_cost (live) + in_destination_cost (KB tier × nights × travelers)
- whether duration_nights is sufficient given one-way travel time
- whether transfer count is practical
```

Regions that exceed total budget or are unreachable within duration are eliminated here.

If total cost exceeds budget → flag as BUDGET_FAIL.

---

### Step 3 — Goal Fit Evaluation

KB stores destination facts, not pre-interpreted fit scores. Meridian interprets fit by mapping traveler goals against destination character, environment, and constraints.

Examples:

```text
adventure seeker
  → remoteness: very_high + physical_exertion: medium + terrain: mountainous
  → strong match

relaxation seeker
  → pace: slow BUT road_conditions_challenging: true + food_accessibility: low
  → logistics burden conflicts with relaxation goal → weak match or eliminate

workation traveler
  → connectivity: low
  → eliminate

celebration / bachelorette
  → social_scene: very_low + nightlife: none
  → eliminate

family with young children
  → altitude_above_4000m: true + medical_facilities_limited: true
  → eliminate

family with teenagers who loves road trips
  → same constraints, but physical_exertion: medium + scenic_value: very_high
  → matcher weighs constraints vs stated preferences → may survive with tradeoff flag
```

The matcher — not the KB — decides fit. The same destination facts produce different conclusions for different travelers.

---

### Step 4 — Scoring

Only score candidates that survive Steps 1–3.

Scoring maps traveler preferences against KB destination facts:

```text
seasonality fit     → best_months match vs acceptable_months match
character fit       → remoteness, scenic_value, pace vs traveler style
environment fit     → climate, terrain, landscape vs weather/terrain preference
crowd fit           → typical_level / peak_level vs crowd_tolerance
experience fit      → walkability, local_authenticity, food_accessibility vs nuanced_preferences
uniqueness          → uniqueness_factor weight for travelers seeking offbeat
constraint severity → how many constraints exist and how much they matter to this traveler
budget headroom     → how comfortably total cost fits budget
```

Nuanced preferences can change ranking. A technically strong region should rank lower if it conflicts with what the traveler actually cares about.

Example:

```text
Traveler: "Wants to walk around and explore. Prefers local feel."
Spiti: walkability: low, local_authenticity: very_high
→ walkability conflict lowers score despite high authenticity
→ Spiti ranks below a region with both high walkability and high authenticity
```

---

### Step 5 — Failure Detection

Evaluate overall result and assign failure state if needed.

```text
HARD_FAIL      → no candidates survive Steps 1–3
SOFT_FAIL      → candidates survive but all have significant tradeoffs
BUDGET_FAIL    → no candidates fit within total budget including travel cost
CONFLICT_FAIL  → traveler preferences are mutually contradictory
MISSING_INPUTS → required inputs not provided
```

If SUCCESS, return ranked regions with reasoning, tradeoffs, and refinement hooks.

---

## Candidate Scope

Meridian matches travel regions — not fixed itineraries or circuits.

```text
Matcher returns: which region fits this traveler
Planner decides: how many places, what route, how many days per place
```

Duration is not used to pre-classify regions. The same region can be done in 2 days or 12 days depending on the traveler. Duration feasibility is checked in Step 2 against travel time from origin — not against a hardcoded region duration.

---

## Outputs

Meridian returns either `SUCCESS` with ranked regions, reasoning, tradeoffs, final recommendation, and refinement hooks; or a failure state from Step 5.

The exact response contract lives in [Trip Matcher API contracts](API_CONTRACTS.md).

Every Meridian response must include:

```text
generated_at → timestamp for when the recommendation was produced
version      → matcher version, for example matcher_v1
```

`version` should change when prompt behavior, KB schema/versioning, scoring logic, or response semantics change enough to affect recommendation output. This makes stored recommendations debuggable later.

---

## Refinement Hooks

Refinement hooks are for Scout, not direct traveler display.

Examples:

```text
weakest_scoring_factor
constraint_with_highest_elimination
budget_headroom
seasonality_note
nuanced_preference_gaps
travel_time_vs_duration_flag
```

Scout uses these to ask specific follow-up questions.

---

## Knowledge Base

Meridian reads the Destination Knowledge Base during Step 1 filtering and Step 4 scoring.

The KB stores traveler-agnostic destination facts only. Meridian is the only component that interprets those facts against traveler preferences.

```text
KB tells Meridian what a region is.
Meridian decides whether it fits this traveler.
Planner decides how to execute the trip.
```

Current direction:

```text
GitHub YAML files
  -> ingest script
      -> Supabase Postgres
          -> structured filtering (Step 1)
          -> fact retrieval for scoring (Step 4)
```

Future direction:

```text
structured filtering + semantic retrieval (pgvector)
```

KB schema details live in [KB schema](../KB/KB_SCHEMA.md).
