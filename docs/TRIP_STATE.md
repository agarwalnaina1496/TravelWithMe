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
    "generate_ready": false,
    "conversation_context": {
      "last_scout_message": null,
      "awaiting": null
    },
    "last_recommendations": null
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
ready
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
ready    -> traveler has indicated they want recommendations now
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

`selected_option` is set when the traveler confirms:

```json
{
  "type": "destination",
  "id": "pondicherry_puducherry"
}
```

or:

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
  "generate_ready": false,
  "conversation_context": {
    "last_scout_message": "How many nights are you thinking?",
    "awaiting": "duration_nights"
  },
  "last_recommendations": null
}
```

`generate_ready` represents traveler intent, not system readiness.

It means the traveler has indicated they want destination recommendations generated at this point in the conversation.

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

The system may technically have enough structured inputs before the traveler is ready. In that case, `generate_ready` should remain `false` until the traveler signals recommendation intent.

`conversation_context` carries only enough context for Scout to resume gracefully. It is not a conversation transcript.

`last_recommendations` stores the latest Meridian output so Scout can present or re-engage without regenerating.

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
| Scout reflects recommendation intent | `ready` |
| User sends message after `ready` | `matching` |
| Destination or circuit confirmed | `matched` |
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

Reads:

```text
trip_context.required_inputs
trip_context.preferences
matcher_state
```

Writes:

```text
trip_context.required_inputs
trip_context.preferences
trip_context.selected_option
matcher_state.generate_ready
matcher_state.conversation_context
matcher_state.last_recommendations
stage
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
