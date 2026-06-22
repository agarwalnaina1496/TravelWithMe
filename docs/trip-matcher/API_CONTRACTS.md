# Trip Matcher API Contracts

This document records the API contract between the UI and Backend.

## Overview

Trip Matcher has two primary calls:

```text
Scout    -> every user interaction
Meridian -> only after the traveler taps Generate
```

Trip Matcher logic is stateless:

```text
- no conversation history
- no session state
- every call operates on complete TripState
```

For the canonical state model, field ownership, storage model, and stage transitions, see [TripState](../TRIP_STATE.md).

## Endpoints

```text
POST /scout
POST /meridian
```

## POST /scout

Handles all Scout interactions.

Scout is called for:

```text
- regular user messages
- destination or circuit confirmation
```

Scout operates on complete `TripState` for every call.

Current MVP request model:

```text
UI sends full TripState.
```

Future database-backed request model:

```text
UI may send trip_id.
TripState may be loaded before Scout runs.
```

The invariant is:

```text
Scout gets complete TripState.
Scout does not rely on hidden memory outside TripState.
```

Every Scout response returns only a `state_delta`. The UI deep-merges `state_delta` back into `TripState` and stores the updated result.

### Request

```json
{
  "trip_state": {},
  "message": "string | null"
}
```

Fields:

```text
trip_state -> full current TripState from localStorage
message    -> latest user message, or null
```

`message: string` means regular user interaction.

`message: null` is not used as a post-Meridian presentation mode. Meridian returns presentable recommendation content, and the UI renders it directly.

### Request Example: Regular Turn

```json
{
  "trip_state": {
    "trip_id": "trip_7f3a9c",
    "status": "free",
    "stage": "matching",
    "trip_context": {
      "required_inputs": {
        "origin_city": "Bengaluru",
        "budget": 30000,
        "budget_unit": "total",
        "duration_nights": null,
        "num_travelers": null,
        "travel_month": "September"
      },
      "preferences": {
        "trip_goal": {
          "value": "Bachelorette",
          "confidence": "explicit"
        },
        "group_type": "friends"
      },
      "selected_option": null
    },
    "matcher_state": {
      "recommendation_intent": false,
      "conversation_context": {
        "last_scout_message": "What's your total budget for the trip?",
        "awaiting": "budget"
      },
      "last_recommendations": null
    },
    "planner_state": null
  },
  "message": "30k total, 3 nights, we are 4 people"
}
```

### Response

Scout always returns the same top-level shape:

```json
{
  "message": "string",
  "state_delta": {
    "stage": "string | omit if unchanged",
    "trip_context": {
      "required_inputs": {},
      "preferences": {},
      "selected_option": {
        "type": "destination | circuit",
        "id": "string"
      }
    },
    "matcher_state": {
      "recommendation_intent": "boolean | omit if unchanged",
      "conversation_context": {
        "last_scout_message": "string",
        "awaiting": "string | null"
      }
    }
  }
}
```

Rules:

```text
- UI deep-merges state_delta into TripState.
- UI does not replace full sections unless the delta explicitly contains replacement values.
- Only changed fields should appear.
- Omit sections with no changes.
- matcher_state.conversation_context.last_scout_message should be included.
```

### Response Example: Still Collecting

```json
{
  "message": "Got it - INR 30,000 total, 3 nights, 4 people from Bengaluru. Is there anything else you'd like me to factor in?",
  "state_delta": {
    "stage": "matching",
    "trip_context": {
      "required_inputs": {
        "budget": 30000,
        "budget_unit": "total",
        "duration_nights": 3,
        "num_travelers": 4
      },
      "preferences": {}
    },
    "matcher_state": {
      "recommendation_intent": false,
      "conversation_context": {
        "last_scout_message": "Got it - INR 30,000 total, 3 nights, 4 people from Bengaluru. Is there anything else you'd like me to factor in?",
        "awaiting": "additional_preferences"
      }
    }
  }
}
```

### Response Example: Recommendation Intent

```json
{
  "message": "Got it - I'll generate recommendations from what you've shared so far.",
  "state_delta": {
    "stage": "ready",
    "trip_context": {
      "required_inputs": {},
      "preferences": {
        "nuanced_preferences": [
          "Wants a chill celebration vibe, not a big party scene"
        ]
      }
    },
    "matcher_state": {
      "recommendation_intent": true,
      "conversation_context": {
        "last_scout_message": "Got it - I'll generate recommendations from what you've shared so far.",
        "awaiting": null
      }
    }
  }
}
```

### Response Example: Option Confirmed

```json
{
  "message": "Pondicherry it is. Great choice for a September bachelorette trip.",
  "state_delta": {
    "stage": "matched",
    "trip_context": {
      "required_inputs": {},
      "preferences": {},
      "selected_option": {
        "type": "destination",
        "id": "pondicherry_puducherry"
      }
    },
    "matcher_state": {
      "recommendation_intent": false,
      "conversation_context": {
        "last_scout_message": "Pondicherry it is. Great choice for a September bachelorette trip.",
        "awaiting": null
      }
    }
  }
}
```

## POST /meridian

Called only after the traveler taps Generate.

Scout never calls Meridian directly.

The traveler decides when to generate recommendations.

Scout passes that traveler intent to the UI by returning:

```text
state_delta.matcher_state.recommendation_intent = true
```

The UI uses this flag to display the Generate Recommendations button. Meridian is called only after the traveler taps that button.

`recommendation_intent` should be set because the traveler asked for recommendations, not because Scout inferred readiness or because the minimum structured inputs are present.

### Request

The UI sends `trip_state.trip_context` directly:

```json
{
  "trip_context": {
    "required_inputs": {
      "origin_city": "Bengaluru",
      "budget": 30000,
      "budget_unit": "total",
      "duration_nights": 3,
      "num_travelers": 4,
      "travel_month": "September"
    },
    "preferences": {
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
  }
}
```

### Success Response

```json
{
  "status": "SUCCESS",
  "generated_at": "2025-09-15T10:45:00Z",
  "version": "matcher_v1",
  "trip_type": "single",
  "budget_basis": {
    "total": 30000,
    "per_person": 7500,
    "num_travelers": 4
  },
  "options": [
    {
      "rank": 1,
      "type": "single",
      "name": "Pondicherry (Puducherry)",
      "destination_id": "pondicherry_puducherry",
      "match_sections": [
        {
          "type": "budget",
          "heading": "Realistic budget breakdown (3 nights, 4 people)",
          "stay": {
            "notes": "Boutique guesthouse near promenade",
            "estimate_per_person": 2625,
            "estimate_group": 10500,
            "assumption": "avg ~3500 INR/night for whole unit"
          },
          "food": {
            "notes": "Cafes, beachside dinners, mix of mid-range and cheap eats",
            "estimate_per_person": 1200,
            "estimate_group": 4800,
            "assumption": "~400 INR/day/person"
          },
          "travel": {
            "notes": "AC Volvo / private shared taxi from Bengaluru",
            "estimate_per_person": 1200,
            "estimate_group": 4800,
            "assumption": "1000-1500 INR per person roundtrip by bus"
          },
          "activities": {
            "notes": "Auroville visit, beach time, photography spots",
            "estimate_per_person": 500,
            "estimate_group": 2000,
            "assumption": "light paid activities"
          },
          "local_transport": {
            "notes": "Scooter rental / tuk-tuks",
            "estimate_per_person": 500,
            "estimate_group": 2000,
            "assumption": "scooter 300-500 INR/day shared"
          },
          "per_person_total": 6025,
          "group_total": 24100,
          "budget_given": 30000,
          "verdict": "comfortable"
        },
        {
          "type": "trip_goal",
          "heading": "Bachelorette fit",
          "points": [
            "Boutique cafes, pastel streets, and beachfront promenades for photos and small celebrations",
            "Private guesthouse options for group privacy and in-house gatherings",
            "A few lively bars ideal for a 4-person group - celebratory without overwhelming"
          ]
        },
        {
          "type": "crowd_preference",
          "heading": "Quieter than usual in September",
          "points": [
            "Shoulder season - town is quieter than peak",
            "Some weekend pockets busier but overall not crowded",
            "More intimate than peak-season Goa"
          ]
        },
        {
          "type": "weather",
          "heading": "Some rain expected - plan flexibly",
          "contextual": true,
          "points": [
            "Tail of monsoon - warm, humid, occasional showers",
            "Rain usually short bursts - beach time still possible",
            "Auroville stays lush and photogenic after rain"
          ]
        },
        {
          "type": "reachability",
          "heading": "Practical from Bengaluru",
          "points": [
            "~6-7 hours by road - AC bus or shared taxi",
            "Overnight bus saves daytime for the trip",
            "No flight needed for a 3-night trip"
          ]
        }
      ],
      "tradeoffs": [
        {
          "point": "Nightlife is low-key - better for intimate celebrations than large party nights",
          "affects": "trip_goal"
        },
        {
          "point": "September rain can interrupt beach plans on short notice",
          "affects": "weather"
        }
      ]
    }
  ],
  "final_recommendation": {
    "best_match": "Pondicherry (Puducherry)",
    "best_match_reason": "Best balance of bachelorette vibe, manageable travel, budget comfort, and September quiet",
    "alternative_1": "Hampi (Karnataka)",
    "alternative_1_reason": "Most budget-friendly, highly photogenic, great for a creative intimate bachelorette - leaves headroom for extras",
    "alternative_2": "Goa (North Goa - budget approach)",
    "alternative_2_reason": "Classic choice - possible within budget only on bus + budget stays. September lowers crowds but also limits nightlife"
  },
  "refinement_hooks": {
    "weakest_scoring_factor": "Nightlife intensity - options vary in party energy; Pondicherry and Hampi are mellow vs peak-season Goa",
    "constraint_with_highest_elimination": "Budget (INR 30000 total) - rules out flying and mid-range stays for Goa",
    "budget_headroom": "comfortable",
    "seasonality_note": "September is monsoon tail - expect rain and humidity. Hampi and Pondicherry stay photogenic; Goa's beach nights are unreliable",
    "nuanced_preference_gaps": null
  }
}
```

### Failure Response

Meridian failures are expected business outcomes, not infrastructure errors.
Failure responses should include the same metadata fields as success responses so debugging can identify which matcher version produced the outcome.

```json
{
  "status": "HARD_FAIL",
  "generated_at": "2025-09-15T10:45:00Z",
  "version": "matcher_v1",
  "message": "No destinations matched all the stated constraints",
  "eliminating_constraints": [
    "crowd_tolerance: low",
    "travel_month: September"
  ],
  "relaxation_suggestions": [
    "Consider October - crowds similar but rain significantly lower",
    "Moderate crowd tolerance to balanced"
  ],
  "surviving_destinations": []
}
```

Supported failure statuses:

```text
HARD_FAIL
SOFT_FAIL
BUDGET_FAIL
CONFLICT_FAIL
MISSING_INPUTS
API_ERROR
```

`API_ERROR` is used only for infrastructure failures, not normal Meridian reasoning failures.

## Full UI Flow

### Input Collection Loop

```text
User sends message
  -> POST /scout
       body: { trip_state, message: "..." }
  -> UI deep-merges state_delta into trip_state
  -> UI writes trip_state to localStorage
  -> UI renders response.message

if recommendation_intent = false:
  continue conversation

if recommendation_intent = true:
  display Generate Recommendations because the traveler asked for recommendations
```

### Generate Recommendations

```text
User taps Generate Recommendations
  -> button enters loading state
  -> POST /meridian
       body: { trip_context: trip_state.trip_context }
  -> write full Meridian response to:
       trip_state.matcher_state.last_recommendations
  -> render Meridian response in the UI
  -> clear recommendation_intent
```

Meridian output, success or failure, always goes to `last_recommendations`.

`generated_at` and `version` must be preserved in `last_recommendations`. They identify when the recommendation was produced and which matcher contract, prompt set, KB version, and scoring logic produced it.

### Confirmation

```text
User confirms destination or circuit
  -> POST /scout
       body: { trip_state, message: "Let's go with Pondicherry" }
  -> Scout returns:
       state_delta.trip_context.selected_option = { type, id }
       state_delta.stage = "matched"
  -> UI deep-merges into localStorage
  -> Trip Matcher complete
```

### Page Refresh

```text
User refreshes
  -> UI reads trip_state from localStorage

stage = "new" or "matching":
  UI resumes from conversation_context

stage = "ready":
  display Generate Recommendations

stage = "matched":
  show confirmed destination
  no Scout call needed unless user re-enters Matcher
```

## Error Handling

### Scout Failure

```text
POST /scout fails with 5xx or network error
  -> UI does not update localStorage
  -> UI shows retry message
  -> user retries with same trip_state
```

### Meridian Infrastructure Failure

```text
POST /meridian fails with 5xx or network error
  -> write { status: "API_ERROR", generated_at, version } to last_recommendations
  -> UI shows retry state
```

### Meridian Business Failure

```text
Meridian returns HARD_FAIL / SOFT_FAIL / BUDGET_FAIL / CONFLICT_FAIL
  -> write failure response to last_recommendations
  -> UI renders Meridian response
```
