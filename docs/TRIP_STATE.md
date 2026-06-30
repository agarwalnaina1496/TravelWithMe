# TripState

`TripState` is the source of truth.

### Purpose

`TripState` represents one trip.

It is shared across phases:

```text
Trip Matcher reads/writes matching state.
Trip Planner will later read trip_context and write planner_state.
Booking and Concierge can build on the same trip lifecycle later.
```

### Design Principles

#### Single Trip Scope

One `TripState` object represents one trip only.

Multiple trips for the same traveler must have separate `TripState` records.

#### Progressive Discovery

`TripState` reflects only what is currently known about the trip.

Do not create placeholder fields for information that has not been discovered.

The exceptions are:

```text
trip_context.required_inputs
status
stage
```

`required_inputs` pre-defines the six fields Trip Matcher needs. Lifecycle fields are always present.

#### Separation Of Concerns

`TripState` is split into objects with clear ownership:

| Object | Owner | Lifecycle |
|---|---|---|
| `trip_context` | The trip | Created at start, lives until trip is done. Read by Matcher and Planner. |
| `matcher_state` | Trip Matcher | Active during matching. Not touched by Planner. |
| `planner_state` | Trip Planner | Null until planning starts. Not touched by Matcher. |
| `traveler_profile` | User profile | Not in TripState. Managed separately later. |

#### Two-Zone Model

```text
free      -> exploratory and reversible
committed -> payment made; stage moves forward only
```

### Top-Level Shape

```json
{
  "trip_id": "trip_7f3a9c",
  "status": "free",
  "stage": "matching",
  "trip_context": {
    "required_inputs": {
      "origin_city": null,
      "budget": null,
      "budget_unit": null,
      "duration_nights": null,
      "num_travelers": null,
      "travel_month": null
    },
    "preferences": {},
    "selected_option": null
  },
  "matcher_state": {
    "recommendation_intent": false,
    "conversation_context": {
      "last_scout_message": null,
      "awaiting": null
    },
    "recommendations": [],
    "rejected_options": []
  },
  "planner_state": null
}
```

### Key Rules

```text
- One TripState object represents one trip only.
- TripState reflects what is currently known.
- Do not create placeholder fields for unknown preferences.
- required_inputs are the only predefined null trip input fields.
- trip_context carries trip-level context forward to later phases.
- matcher_state is owned by Trip Matcher.
- planner_state is null until Trip Planner starts.
- traveler_profile is not part of TripState.
```

### Lifecycle Fields

`status`:

```text
free      -> exploratory and reversible
committed -> payment made; stage moves forward only
```

`stage`:

```text
new
matching
recommendation_ready
recommended
matched
planning
planned
booked
done
```

For Trip Matcher:

```text
new      -> TripState created
matching -> Scout is collecting or refining inputs
recommendation_ready -> traveler has told Scout they want recommendations now
recommended -> Meridian recommendations have been generated and stored
matched  -> traveler confirmed a destination or circuit
```

### trip_context

`trip_context` is shared trip-level context.

`required_inputs`:

```json
{
  "origin_city": "Bengaluru",
  "budget": 30000,
  "budget_unit": "total",
  "duration_nights": 3,
  "num_travelers": 4,
  "travel_month": "September"
}
```

`preferences` is dynamic. It includes only discovered information:

```json
{
  "trip_goal": {
    "value": "Bachelorette",
    "confidence": "explicit"
  },
  "crowd_tolerance": {
    "value": "low",
    "confidence": "high"
  },
  "group_type": "friends",
  "travel_style": ["relaxed", "social"],
  "nuanced_preferences": [
    "Wants a chill celebration vibe, not a big party scene",
    "Prefers boutique or aesthetic stays"
  ]
}
```

Scalar preferences should include a confidence score when useful:

```text
explicit -> traveler stated it directly
high     -> strongly implied from context
medium   -> reasonably inferred
low      -> tentative signal
```

Arrays such as `travel_style` and `nuanced_preferences` do not need confidence wrappers.

Simple categoricals such as `group_type` can be plain values.

`trip_goal` belongs in `preferences`, not `required_inputs`, because it is a preference signal with confidence rather than a hard input field.

`selected_option` is set when the traveler confirms a destination or circuit:

```json
{
  "type": "destination | circuit",
  "id": "..."
}
```

`selected_option` means the traveler has made a final choice. It should remain `null` while the traveler is comparing or refining recommendations.

Destination example:

```json
{
  "type": "destination",
  "id": "pondicherry_puducherry"
}
```

Circuit example:

```json
{
  "type": "circuit",
  "id": "rajasthan_classic"
}
```

### matcher_state

`matcher_state` is owned by Trip Matcher.

```json
{
  "recommendation_intent": false,
  "conversation_context": {
    "last_scout_message": "How many nights are you thinking?",
    "awaiting": "duration_nights"
  },
  "recommendations": [],
  "rejected_options": []
}
```

`recommendation_intent` represents traveler intent, not system readiness or UI behavior.

It means the traveler has told Scout they want destination recommendations now.

Examples of recommendation intent:

```text
"generate"
"show recommendations"
"what do you suggest?"
"I'm ready"
"surprise me"
"that's enough, show me options"
```

It does not mean the system merely has enough inputs to generate.

The system may technically have enough structured inputs before the traveler is ready. In that case, `recommendation_intent` should remain `false` until the traveler tells Scout they are ready for recommendations.

`conversation_context` carries only enough context for Scout to resume gracefully. It is not a conversation transcript.

`recommendations` stores Meridian output history. Each successful Meridian response, including business failures such as `HARD_FAIL` or `BUDGET_FAIL`, is appended to this array. The latest recommendation is the last item in the array.

Infrastructure failures such as network errors or 5xx responses are not appended to `recommendations`; the UI should show a retry state and keep the existing trip state.

`rejected_options` is optional legacy matcher state for recommendation options the traveler rejected during older refinement flows:

```json
[
  {
    "type": "destination",
    "id": "goa_north_budget",
    "name": "Goa",
    "reason": "user_rejected"
  }
]
```

Rejected options are matcher-specific state, not durable trip preferences. Current refinement should not depend on this field.

### planner_state

`planner_state` is `null` during Trip Matcher.

It becomes active only after the traveler enters Trip Planner.

Example:

```json
{
  "conversation_context": {
    "last_message": "Which part of Pondicherry would you prefer to stay in?",
    "awaiting": "accommodation_area"
  },
  "preferences": {
    "accommodation_area": "White Town",
    "transport_preference": "scooter rental",
    "meal_preference": "local restaurants over hotel food"
  },
  "itinerary": null
}
```

`planner_state.preferences` contains planning-specific preferences that emerge during the planning conversation. These are more granular than trip-level signals.

`planner_state.itinerary` is the output of Trip Planner when complete.

### traveler_profile

`traveler_profile` is not part of `TripState`.

Persistent user-level preferences, travel history, and learned behavior should be managed separately when user accounts are introduced.

At that point, the backend can combine:

```text
TripState + TravelerProfile
```

before calling a feature. `TripState` does not need to contain the profile.

### Storage

Storage is an implementation detail:

```text
Today: localStorage
Later: database
```

The semantic contract should not change when storage moves from localStorage to a database: features still operate on complete `TripState`.

The transport shape may change. In the MVP, the UI sends full `TripState`; later, it might be loaded from a database.

## Stage Transition Reference

### Free Zone

| Action | Stage becomes |
|---|---|
| TripState created | `new` |
| Scout starts collecting | `matching` |
| UI receives Scout recommendation intent | `recommendation_ready` |
| Meridian response is returned to UI | `recommended` |
| User sends message after `recommendation_ready` | `matching` and `recommendation_intent` becomes `false` |
| User refines recommendations after `recommended` | `matching` and `recommendation_intent` becomes `false` |
| Destination or circuit confirmed | `matched` |
| User refines after `matched` | `matching` and `selected_option` becomes `null` |
| User returns to Matcher from Planner | `matching` |
| Planner started | `planning` |
| Plan complete | `planned` |

### Committed Zone

| Action | Stage becomes |
|---|---|
| Payment made | `booked` and `status` becomes `committed` |
| Trip completed | `done` |

## Feature Read/Write Ownership

### Trip Matcher

Trip Matcher reads:

```text
trip_context.required_inputs
trip_context.preferences
matcher_state
```

Scout returns deltas for:

```text
trip_context.required_inputs
trip_context.preferences
matcher_state.recommendation_intent
matcher_state.conversation_context
```

Meridian returns recommendation output for:

```text
matcher_state.recommendations
```

The UI owns deterministic matcher state writes:

```text
stage
trip_context.selected_option
matcher_state.recommendations
matcher_state.rejected_options
```

### Trip Planner

Reads:

```text
trip_context
```

Writes:

```text
planner_state
stage
```

Does not read or write:

```text
matcher_state
```

Trip Planner should work whether or not Trip Matcher was used. If required planning context is missing, Planner asks the user and writes the answer to `TripState`.
