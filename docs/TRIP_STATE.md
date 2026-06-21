## TripState

`TripState` is the source of truth.

### Purpose

`TripState` represents one trip.

It is shared across phases:

```text
Trip Matcher reads/writes matching state.
Trip Planner will later read trip_context and write planner_state.
Booking and Concierge can build on the same trip lifecycle later.
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

### Storage

Storage is an implementation detail:

```text
Today: localStorage
Later: database
```

The semantic contract should not change when storage moves from localStorage to a database: It still operates on complete `TripState`.

The transport shape may change. In the MVP, the UI sends full `TripState`; later, it might be laoded via database.
