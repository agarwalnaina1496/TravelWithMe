# Meridian

Meridian is the Trip Matcher decision engine.

It receives complete trip context, runs destination or circuit matching, and returns ranked recommendations with reasoning, tradeoffs, and refinement hooks.

Meridian is stateless. It does not hold conversation history. It does not decide when to run. The traveler triggers generation from the UI after Scout sets recommendation intent.

## Responsibilities

```text
- validate required matching inputs
- read trip context and preferences
- generate destination or circuit candidates
- eliminate candidates that violate hard constraints
- score surviving candidates
- return a ranked shortlist with clear reasoning
- return failure states when the constraints cannot produce good options
- provide refinement hooks for Scout
```

Scout handles conversation. Meridian handles matching.

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
explicit exclusions
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

## Matching Pipeline

Meridian's matching flow is:

```text
0. Parse preferences and overrides
1. Generate candidates
2. Eliminate hard constraint violations
3. Eliminate poor goal-fit candidates
4. Check transport feasibility
5. Check travel time feasibility
5a. Check travel-to-experience ratio for multi-stop trips
6. Validate geography and route practicality
7. Score and rank survivors
8. Detect failure states
```

The current implementation can evolve, but these are the intended decision responsibilities.

## Preference Priority

Explicit traveler preferences beat defaults.

```text
explicit traveler preference > system defaults > fallback heuristics
```

Examples:

```text
- If the traveler says "no flights", flight-heavy options should be removed.
- If the traveler says "happy to travel 20 hours", default travel-time limits should loosen.
- "Budget is not an issue" should not automatically mean luxury unless the traveler asks for luxury.
```

## Candidate Scope

Duration influences what Meridian considers:

```text
1-3 nights   -> single destinations
4-5 nights   -> single destinations or simple 2-stop combinations
6-8 nights   -> circuits or stronger multi-stop options
9-12 nights  -> extended circuits
```

For longer trips, Meridian should consider whether a circuit gives a better experience than one destination.

## Hard Constraint Elimination

Remove candidates that violate explicit exclusions or constraints.

Examples:

```text
no beaches
no trekking
no nightlife
no flights
avoid rain
prefer cool weather
```

These are eliminations, not soft scoring preferences, when the traveler states them clearly.

## Goal Fit

Remove candidates that fundamentally do not match the trip goal.

Examples:

```text
relaxation   -> avoid high-stimulation, logistics-heavy options
celebration  -> avoid places without enough social or stay options
adventure    -> avoid purely passive destinations
workation    -> avoid poor-connectivity destinations
wildlife     -> avoid weak wildlife destinations
```

## Travel Feasibility

Meridian should check:

```text
- whether transport cost fits the budget
- whether one-way travel time is reasonable for the trip length
- whether transfer count is practical
- whether route logic makes sense for circuits
```

For multi-stop trips, internal travel should stay proportionate to time on ground. If the route eats too much of the trip, Meridian should either trim the circuit, flag it as a tradeoff, or eliminate it.

## Scoring

Only score candidates that survive eliminations.

Useful scoring factors:

```text
seasonality fit
travel style fit
group fit
destination or circuit strengths
uniqueness
route logic for circuits
experience variety for circuits
nuanced preference fit
```

Nuanced preferences can change ranking. A technically strong destination should rank lower if it conflicts with what the traveler actually cares about.

## Outputs

Meridian returns either:

```text
SUCCESS
```

with ranked options, reasoning, tradeoffs, final recommendation, and refinement hooks; or a business failure:

```text
HARD_FAIL
SOFT_FAIL
BUDGET_FAIL
CONFLICT_FAIL
MISSING_INPUTS
```

The exact response contract lives in [Trip Matcher API contracts](API_CONTRACTS.md).

Every Meridian response must include:

```text
generated_at -> timestamp for when the recommendation was produced
version      -> matcher version, for example matcher_v1
```

`version` should change when prompt behavior, KB schema/versioning, scoring logic, or response semantics change enough to affect recommendation output. This makes stored recommendations debuggable later.

## Refinement Hooks

Refinement hooks are for Scout, not direct traveler display.

Examples:

```text
weakest_scoring_factor
constraint_with_highest_elimination
budget_headroom
seasonality_note
nuanced_preference_gaps
travel_to_experience_flag
```

Scout uses these to ask specific follow-up questions.

## Knowledge Base

Meridian reads the Destination Knowledge Base during candidate generation and scoring.

Current direction:

```text
GitHub YAML files
  -> ingest script
      -> Supabase Postgres
          -> structured filtering
```

Future direction:

```text
structured filtering + semantic retrieval
```

KB schema details live in [KB schema](../KB/KB_SCHEMA.md).
